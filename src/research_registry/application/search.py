from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any

from ..contracts.v2 import ResearchSearchRequest, ResearchSearchResponse, SearchHitV2
from ..persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
    RetrievalCandidate,
)


MAX_SEARCH_RESPONSE_BYTES = 131_072
_MAX_CANDIDATES = 1_001
_MAX_TOTAL_CANDIDATES = _MAX_CANDIDATES * 6
_TOKEN = re.compile(r"[\w./:-]+", re.UNICODE)


@dataclass(frozen=True)
class SearchResultDTO:
    hit: SearchHitV2
    sort_key: tuple[float, str, str]


class ResearchSearchService:
    def __init__(self, retrieval: CurrentRetrievalAdapter):
        self.retrieval = retrieval

    def search(
        self,
        request: ResearchSearchRequest,
        *,
        access: ReadAccess,
    ) -> ResearchSearchResponse:
        fingerprint = self._fingerprint(request)
        offset = self._decode_cursor(request.cursor, fingerprint)
        candidates = self.retrieval.list_candidates(
            kinds=request.kinds,
            access=access,
            max_per_kind=_MAX_CANDIDATES,
        )
        results = [
            result
            for candidate in candidates
            if (result := self._match(candidate, request)) is not None
        ]
        results.sort(key=lambda result: result.sort_key, reverse=True)
        page = results[offset : offset + request.limit]
        hits: list[SearchHitV2] = []
        for result in page:
            hits.append(result.hit)
            provisional = ResearchSearchResponse(
                protocol="research-search-result/v2",
                query=request.query,
                hits=hits,
                next_cursor=None,
            )
            if len(provisional.model_dump_json().encode("utf-8")) > MAX_SEARCH_RESPONSE_BYTES:
                hits.pop()
                break
        consumed = len(hits)
        has_more = offset + consumed < len(results)
        next_cursor = (
            self._encode_cursor(offset + consumed, fingerprint)
            if has_more and consumed
            else None
        )
        return ResearchSearchResponse(
            protocol="research-search-result/v2",
            query=request.query,
            hits=hits,
            next_cursor=next_cursor,
        )

    def _match(
        self,
        candidate: RetrievalCandidate,
        request: ResearchSearchRequest,
    ) -> SearchResultDTO | None:
        if not request.include_rejected and candidate.status in {
            "rejected",
            "superseded",
        }:
            return None
        if request.review_states and candidate.review_state not in request.review_states:
            return None
        if (
            request.conflict_states
            and candidate.conflict_state not in request.conflict_states
        ):
            return None
        if request.freshness and candidate.freshness not in request.freshness:
            return None
        scope = request.scope
        if scope is not None:
            if scope.repository and candidate.repository != scope.repository:
                return None
            if scope.paths and not any(
                self._path_matches(candidate.path, pattern)
                for pattern in scope.paths
            ):
                return None
            if scope.topic_ids and candidate.topic_id not in scope.topic_ids:
                return None
            if scope.source_types and candidate.source_type not in scope.source_types:
                return None
            created = self._datetime(candidate.created_at)
            if scope.created_after and (
                created is None or created < scope.created_after
            ):
                return None
            if scope.created_before and (
                created is None or created > scope.created_before
            ):
                return None

        query = " ".join(request.query.casefold().split())
        haystack = " ".join(candidate.search_text.casefold().split())
        title = " ".join(candidate.title.casefold().split())
        tokens = list(dict.fromkeys(_TOKEN.findall(query)))[:20]
        matched_by: list[str] = []
        exact = 0.0
        lexical = 0.0
        if query == candidate.id.casefold():
            exact = 1.0
            matched_by.append("exact registry id")
        if candidate.url and query == candidate.url.casefold():
            exact = 1.0
            matched_by.append("exact source locator")
        if query == title:
            exact = max(exact, 0.95)
            matched_by.append("exact title")
        if query in haystack:
            lexical = 1.0
            matched_by.append("exact phrase")
        elif tokens:
            matched = sum(token in haystack for token in tokens)
            lexical = matched / len(tokens)
            if matched:
                matched_by.append(f"current substring retrieval ({matched}/{len(tokens)})")
        if exact == 0 and lexical == 0:
            return None

        review = 0.05 if candidate.review_state == "reviewed" else 0.0
        freshness = (
            0.03
            if candidate.freshness == "fresh"
            else -0.03
            if candidate.freshness in {"stale", "needs_refresh"}
            else 0.0
        )
        conflict = -0.02 if candidate.conflict_state == "conflicted" else 0.0
        score = min(
            1.0,
            max(0.0, 0.55 * max(exact, lexical) + 0.35 * lexical + review + freshness + conflict),
        )
        components = (
            {
                "exact": round(exact, 6),
                "lexical": round(lexical, 6),
                "review": review,
                "freshness": freshness,
                "conflict": conflict,
            }
            if request.explain
            else {}
        )
        hit = SearchHitV2(
            id=candidate.id,
            kind=candidate.kind,  # type: ignore[arg-type]
            title=candidate.title[:500],
            summary=candidate.summary[:1_500],
            score=round(score, 6),
            score_components=components,
            matched_by=matched_by if request.explain else [],
            review_state=candidate.review_state,  # type: ignore[arg-type]
            conflict_state=candidate.conflict_state,  # type: ignore[arg-type]
            freshness=candidate.freshness,  # type: ignore[arg-type]
            evidence_count=candidate.evidence_count,
            updated_at=self._datetime(candidate.updated_at),
            url=candidate.url,
        )
        return SearchResultDTO(
            hit=hit,
            sort_key=(score, candidate.updated_at or "", candidate.id),
        )

    @staticmethod
    def _path_matches(path: str | None, pattern: str) -> bool:
        if path is None:
            return False
        if pattern.endswith("/**"):
            return path.startswith(pattern[:-3].rstrip("/") + "/")
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    @staticmethod
    def _datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _fingerprint(request: ResearchSearchRequest) -> str:
        payload = request.model_dump(
            mode="json", exclude={"cursor", "limit"}, exclude_none=True
        )
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()[:24]

    @staticmethod
    def _encode_cursor(offset: int, fingerprint: str) -> str:
        raw = json.dumps(
            {"v": 1, "offset": offset, "query": fingerprint},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, fingerprint: str) -> int:
        if cursor is None:
            return 0
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            )
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or payload.get("query") != fingerprint
                or not isinstance(payload.get("offset"), int)
                or payload["offset"] < 0
                or payload["offset"] > _MAX_TOTAL_CANDIDATES
            ):
                raise ValueError
            return payload["offset"]
        except (
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            raise ValueError(
                "INVALID_CURSOR: The search cursor is invalid."
            ) from None
