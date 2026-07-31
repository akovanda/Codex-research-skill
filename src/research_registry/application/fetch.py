from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, JsonValue

from ..contracts.common import ClosedModel
from ..persistence.read_adapter import CurrentRetrievalAdapter, ReadAccess, ReadRecord


MAX_GET_RESPONSE_BYTES = 131_072
UNTRUSTED_CONTENT_LABEL = "untrusted research material"


class ResearchGetResult(ClosedModel):
    protocol: Literal["research-get-result/v2"]
    id: str
    kind: Literal[
        "question",
        "source",
        "source_version",
        "evidence",
        "claim",
        "report",
        "refresh",
    ]
    title: str
    text: str
    url: str | None
    review_state: str | None
    conflict_state: str | None
    freshness: str | None
    updated_at: str | None
    content_label: Literal["untrusted research material"]
    record: dict[str, JsonValue] = Field(max_length=50)
    includes: dict[str, JsonValue] = Field(max_length=10)
    truncated: bool


class ResearchFetchService:
    def __init__(self, retrieval: CurrentRetrievalAdapter):
        self.retrieval = retrieval

    def get(
        self,
        *,
        record_id: str,
        include: list[str],
        depth: int,
        access: ReadAccess,
    ) -> ResearchGetResult:
        record = self.retrieval.get_record(record_id, access=access)
        if record is None:
            raise ValueError("RECORD_NOT_FOUND: The research record was not found.")
        includes: dict[str, JsonValue] = {}
        if depth > 0:
            self._hydrate(record, include, access=access, output=includes)
        result = ResearchGetResult(
            protocol="research-get-result/v2",
            id=record.id,
            kind=record.kind,  # type: ignore[arg-type]
            title=record.title[:500],
            text=record.text[:50_000],
            url=record.url,
            review_state=record.review_state,
            conflict_state=record.conflict_state,
            freshness=record.freshness,
            updated_at=record.updated_at,
            content_label=UNTRUSTED_CONTENT_LABEL,
            record=self._compact_json(record.data),
            includes=includes,
            truncated=False,
        )
        return self._bound(result)

    def _hydrate(
        self,
        record: ReadRecord,
        include: list[str],
        *,
        access: ReadAccess,
        output: dict[str, JsonValue],
    ) -> None:
        evidence: list[dict[str, Any]] = []
        if record.kind == "claim" and "current_revision" in include:
            output["current_revision"] = self.retrieval.get_current_revision(
                record.id
            )
        if record.kind == "claim" and "revision_history" in include:
            output["revision_history"] = self.retrieval.list_revisions(record.id)
        if "evidence" in include or "source_versions" in include:
            evidence = self.retrieval.list_evidence(record, access=access)
        if "evidence" in include:
            output["evidence"] = evidence
        if "source_versions" in include:
            source_id = record.id if record.kind == "source" else None
            if record.kind == "source_version":
                version_ids = [record.id]
            else:
                version_ids = [
                    item["source_version_id"] for item in evidence
                ]
            output["source_versions"] = self.retrieval.list_source_versions(
                source_id=source_id,
                version_ids=version_ids,
                access=access,
            )
        if "reports" in include:
            output["reports"] = self.retrieval.list_reports(
                record, access=access
            )
        if "refresh" in include:
            output["refresh"] = self.retrieval.list_refresh(record)
        if "reviews" in include:
            ids = [record.id]
            current = output.get("current_revision")
            if isinstance(current, dict) and isinstance(current.get("id"), str):
                ids.append(current["id"])
            for key in ("evidence", "source_versions", "reports"):
                values = output.get(key)
                if isinstance(values, list):
                    ids.extend(
                        item["id"]
                        for item in values
                        if isinstance(item, dict)
                        and isinstance(item.get("id"), str)
                    )
            output["reviews"] = self.retrieval.list_reviews(entity_ids=ids)

    @staticmethod
    def _bound(result: ResearchGetResult) -> ResearchGetResult:
        if len(result.model_dump_json().encode("utf-8")) <= MAX_GET_RESPONSE_BYTES:
            return result
        includes = dict(result.includes)
        for key in (
            "reviews",
            "revision_history",
            "reports",
            "source_versions",
            "evidence",
        ):
            values = includes.get(key)
            if not isinstance(values, list):
                continue
            while values:
                values.pop()
                candidate = result.model_copy(
                    update={"includes": includes, "truncated": True}
                )
                if (
                    len(candidate.model_dump_json().encode("utf-8"))
                    <= MAX_GET_RESPONSE_BYTES
                ):
                    return candidate
        minimal = result.model_copy(
            update={
                "text": result.text[:4_000],
                "includes": {},
                "record": {},
                "truncated": True,
            }
        )
        if len(minimal.model_dump_json().encode("utf-8")) <= MAX_GET_RESPONSE_BYTES:
            return minimal
        return minimal.model_copy(
            update={
                "title": minimal.title[:500],
                "text": minimal.text[:1_000],
                "url": None,
            }
        )

    @classmethod
    def _compact_json(cls, value: Any, *, depth: int = 0) -> Any:
        if depth >= 5:
            return None
        if isinstance(value, str):
            return value[:4_000]
        if isinstance(value, list):
            return [
                cls._compact_json(item, depth=depth + 1)
                for item in value[:50]
            ]
        if isinstance(value, dict):
            return {
                str(key)[:100]: cls._compact_json(item, depth=depth + 1)
                for key, item in list(value.items())[:50]
            }
        if value is None or isinstance(value, bool | int | float):
            return value
        return str(value)[:4_000]
