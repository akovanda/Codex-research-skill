from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import (
    AnnotationCreate,
    FindingCreate,
    ReportCreate,
    RunCreate,
    SourceCreate,
    SourceSelector,
)
from .service import RegistryService, utc_now


class QueuedAnnotation(BaseModel):
    temp_id: str
    source: SourceCreate
    subject: str
    note: str
    selector: SourceSelector
    quote_text: str | None = None
    confidence: float = 0.6
    freshness_ttl_days: int = 30
    visibility: str = "private"
    author_type: str = "agent"
    model_name: str | None = None
    model_version: str | None = None
    parent_annotation_temp_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class QueuedFinding(BaseModel):
    temp_id: str
    title: str
    subject: str
    claim: str
    annotation_temp_ids: list[str] = Field(min_length=1)
    visibility: str = "private"
    author_type: str = "agent"
    model_name: str | None = None
    model_version: str | None = None
    confidence: float | None = None


class QueuedReport(BaseModel):
    question: str
    subject: str
    summary_md: str
    finding_temp_ids: list[str] = Field(min_length=1)
    visibility: str = "private"
    author_type: str = "agent"
    model_name: str | None = None
    model_version: str | None = None


class QueuedCaptureBundle(BaseModel):
    queue_id: str
    created_at: datetime
    prompt: str
    normalized_topic: str
    model_name: str
    model_version: str
    run: RunCreate
    annotations: list[QueuedAnnotation] = Field(default_factory=list)
    findings: list[QueuedFinding] = Field(default_factory=list)
    report: QueuedReport
    retry_count: int = 0
    last_error: str | None = None
    last_attempted_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        prompt: str,
        normalized_topic: str,
        model_name: str,
        model_version: str,
        run: RunCreate,
        annotations: list[QueuedAnnotation],
        findings: list[QueuedFinding],
        report: QueuedReport,
    ) -> "QueuedCaptureBundle":
        return cls(
            queue_id=f"queue_{uuid4().hex[:12]}",
            created_at=utc_now(),
            prompt=prompt,
            normalized_topic=normalized_topic,
            model_name=model_name,
            model_version=model_version,
            run=run,
            annotations=annotations,
            findings=findings,
            report=report,
        )


class QueueFlushResult(BaseModel):
    flushed_queue_ids: list[str] = Field(default_factory=list)
    failed_queue_ids: list[str] = Field(default_factory=list)
    stored_report_ids: list[str] = Field(default_factory=list)


class CaptureQueue:
    def __init__(self, queue_path: Path):
        self.queue_path = queue_path
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, bundle: QueuedCaptureBundle) -> None:
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(bundle.model_dump_json())
            handle.write("\n")

    def list_pending(self) -> list[QueuedCaptureBundle]:
        if not self.queue_path.exists():
            return []
        bundles: list[QueuedCaptureBundle] = []
        for raw_line in self.queue_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            bundles.append(QueuedCaptureBundle.model_validate_json(line))
        return bundles

    def flush(self, service: RegistryService) -> QueueFlushResult:
        pending = self.list_pending()
        remaining: list[QueuedCaptureBundle] = []
        result = QueueFlushResult()
        for bundle in pending:
            try:
                replay_result = self._replay_bundle(service, bundle)
                result.flushed_queue_ids.append(bundle.queue_id)
                result.stored_report_ids.append(replay_result["report_id"])
            except Exception as exc:
                bundle.retry_count += 1
                bundle.last_error = str(exc)
                bundle.last_attempted_at = utc_now()
                remaining.append(bundle)
                result.failed_queue_ids.append(bundle.queue_id)
        self._write_all(remaining)
        return result

    def _write_all(self, bundles: list[QueuedCaptureBundle]) -> None:
        if not bundles:
            self.queue_path.write_text("", encoding="utf-8")
            return
        with self.queue_path.open("w", encoding="utf-8") as handle:
            for bundle in bundles:
                handle.write(bundle.model_dump_json())
                handle.write("\n")

    def _replay_bundle(self, service: RegistryService, bundle: QueuedCaptureBundle) -> dict[str, str]:
        run = self._ensure_run(service, bundle)
        annotation_map: dict[str, str] = {}
        for annotation in bundle.annotations:
            annotation_map[annotation.temp_id] = self._ensure_annotation(service, run.id, annotation, annotation_map)
        finding_map: dict[str, str] = {}
        for finding in bundle.findings:
            annotation_ids = [annotation_map[temp_id] for temp_id in finding.annotation_temp_ids]
            finding_map[finding.temp_id] = self._ensure_finding(service, run.id, finding, annotation_ids)
        finding_ids = [finding_map[temp_id] for temp_id in bundle.report.finding_temp_ids]
        report_id = self._ensure_report(service, run.id, bundle.report, finding_ids)
        return {"run_id": run.id, "report_id": report_id}

    def _ensure_run(self, service: RegistryService, bundle: QueuedCaptureBundle):
        marker = f"capture_queue_id={bundle.queue_id}"
        with service.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE notes = ? OR notes LIKE ? LIMIT 1",
                (marker, f"%{marker}%"),
            ).fetchone()
        if row:
            return service._run_from_row(row)
        notes = bundle.run.notes.strip() if bundle.run.notes else ""
        notes = f"{notes}\n{marker}".strip()
        return service.create_run(
            RunCreate(
                question=bundle.run.question,
                model_name=bundle.run.model_name,
                model_version=bundle.run.model_version,
                notes=notes,
                visibility=bundle.run.visibility,
                author_type=bundle.run.author_type,
                freshness_ttl_days=bundle.run.freshness_ttl_days,
            )
        )

    def _ensure_annotation(
        self,
        service: RegistryService,
        run_id: str,
        annotation: QueuedAnnotation,
        annotation_map: dict[str, str],
    ) -> str:
        source = service.create_source(annotation.source)
        quote_text = annotation.quote_text or annotation.selector.exact
        quote_hash = service._hash_text(quote_text) if quote_text else None
        fingerprint = service._anchor_fingerprint(source.id, annotation.selector, quote_hash)
        parent_id = annotation_map.get(annotation.parent_annotation_temp_id or "")
        with service.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM annotations
                WHERE source_id = ?
                  AND anchor_fingerprint = ?
                  AND subject = ?
                  AND note = ?
                  AND COALESCE(quote_hash, '') = COALESCE(?, '')
                  AND COALESCE(model_name, '') = COALESCE(?, '')
                  AND COALESCE(model_version, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (
                    source.id,
                    fingerprint,
                    annotation.subject,
                    annotation.note,
                    quote_hash,
                    annotation.model_name,
                    annotation.model_version,
                ),
            ).fetchone()
        if row:
            return row["id"]
        created = service.create_annotation(
            AnnotationCreate(
                source_id=source.id,
                run_id=run_id,
                subject=annotation.subject,
                note=annotation.note,
                selector=annotation.selector,
                quote_text=annotation.quote_text,
                confidence=annotation.confidence,
                freshness_ttl_days=annotation.freshness_ttl_days,
                visibility=annotation.visibility,
                author_type=annotation.author_type,
                model_name=annotation.model_name,
                model_version=annotation.model_version,
                parent_annotation_id=parent_id,
                tags=annotation.tags,
            )
        )
        return created.id

    def _ensure_finding(
        self,
        service: RegistryService,
        run_id: str,
        finding: QueuedFinding,
        annotation_ids: list[str],
    ) -> str:
        with service.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM findings
                WHERE title = ?
                  AND subject = ?
                  AND claim = ?
                  AND COALESCE(model_name, '') = COALESCE(?, '')
                  AND COALESCE(model_version, '') = COALESCE(?, '')
                """,
                (
                    finding.title,
                    finding.subject,
                    finding.claim,
                    finding.model_name,
                    finding.model_version,
                ),
            ).fetchall()
        target_ids = sorted(annotation_ids)
        for row in rows:
            existing = service.get_finding(row["id"], include_private=True)
            if sorted(existing.annotation_ids) == target_ids:
                return existing.id
        created = service.create_finding(
            FindingCreate(
                title=finding.title,
                subject=finding.subject,
                claim=finding.claim,
                annotation_ids=annotation_ids,
                visibility=finding.visibility,
                author_type=finding.author_type,
                model_name=finding.model_name,
                model_version=finding.model_version,
                run_id=run_id,
                confidence=finding.confidence,
            )
        )
        return created.id

    def _ensure_report(
        self,
        service: RegistryService,
        run_id: str,
        report: QueuedReport,
        finding_ids: list[str],
    ) -> str:
        with service.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM reports
                WHERE question = ?
                  AND subject = ?
                  AND summary_md = ?
                  AND COALESCE(model_name, '') = COALESCE(?, '')
                  AND COALESCE(model_version, '') = COALESCE(?, '')
                """,
                (
                    report.question,
                    report.subject,
                    report.summary_md,
                    report.model_name,
                    report.model_version,
                ),
            ).fetchall()
        target_ids = sorted(finding_ids)
        for row in rows:
            existing = service.get_report(row["id"], include_private=True)
            if sorted(existing.finding_ids) == target_ids:
                return existing.id
        created = service.create_report(
            ReportCreate(
                question=report.question,
                subject=report.subject,
                summary_md=report.summary_md,
                finding_ids=finding_ids,
                visibility=report.visibility,
                author_type=report.author_type,
                model_name=report.model_name,
                model_version=report.model_version,
                run_id=run_id,
            )
        )
        return created.id
