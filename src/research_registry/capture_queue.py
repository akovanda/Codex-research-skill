from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .application.deposit import ResearchDepositService
from .backend_client import RegistryBackend
from .contracts.common import ClosedModel
from .contracts.v2 import ResearchDepositRequest, ResearchDepositResult
from .models import (
    BackendStatus,
    ClaimCreate,
    ExcerptCreate,
    FocusTuple,
    GuidancePayload,
    QuestionCreate,
    ReportCreate,
    ResearchSessionCreate,
    RunCreate,
    SourceCreate,
    SourceSelector,
)
from .service import utc_now


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
    backend_url: str | None = None
    backend_name: str | None = None
    namespace_kind: str = "user"
    namespace_id: str = "local"
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
        backend_status: BackendStatus | None = None,
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
            backend_url=backend_status.url if backend_status else None,
            backend_name=backend_status.name if backend_status else None,
            namespace_kind=backend_status.namespace_kind if backend_status else run.namespace_kind,
            namespace_id=backend_status.namespace_id if backend_status else run.namespace_id,
        )


class QueuedV2Deposit(ClosedModel):
    protocol: Literal["research-capture-queue/v2"]
    queue_id: str
    created_at: datetime = Field(strict=False)
    deposit: ResearchDepositRequest
    backend_url: str | None = None
    backend_name: str | None = None
    namespace_kind: str = "user"
    namespace_id: str = "local"
    retry_count: int = 0
    last_error: str | None = None
    last_attempted_at: datetime | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def require_committing_deposit(self) -> "QueuedV2Deposit":
        if self.deposit.validate_only:
            raise ValueError("queued v2 deposits must commit when replayed")
        expected_kind = (
            self.deposit.namespace.kind if self.deposit.namespace else "user"
        )
        expected_id = (
            self.deposit.namespace.id if self.deposit.namespace else "local"
        )
        if (
            self.namespace_kind != expected_kind
            or self.namespace_id != expected_id
        ):
            raise ValueError("queue namespace must match the deposit namespace")
        return self

    @classmethod
    def create(
        cls,
        deposit: ResearchDepositRequest,
        *,
        backend_status: BackendStatus | None = None,
    ) -> "QueuedV2Deposit":
        namespace_kind = deposit.namespace.kind if deposit.namespace else "user"
        namespace_id = deposit.namespace.id if deposit.namespace else "local"
        return cls(
            protocol="research-capture-queue/v2",
            queue_id=f"queue_{uuid4().hex[:12]}",
            created_at=utc_now(),
            deposit=deposit,
            backend_url=backend_status.url if backend_status else None,
            backend_name=backend_status.name if backend_status else None,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )


QueueEnvelope = QueuedCaptureBundle | QueuedV2Deposit


class QueueFlushResult(BaseModel):
    flushed_queue_ids: list[str] = Field(default_factory=list)
    failed_queue_ids: list[str] = Field(default_factory=list)
    stored_report_ids: list[str] = Field(default_factory=list)


class CaptureQueue:
    def __init__(
        self,
        queue_path: Path,
        *,
        deposit_service: ResearchDepositService | None = None,
    ):
        self.queue_path = queue_path
        self.deposit_service = deposit_service
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, bundle: QueueEnvelope) -> None:
        with self._lock():
            with self.queue_path.open("a", encoding="utf-8") as handle:
                handle.write(bundle.model_dump_json(exclude_none=True))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    def list_pending(self) -> list[QueueEnvelope]:
        with self._lock():
            return self._list_pending_unlocked()

    def _list_pending_unlocked(self) -> list[QueueEnvelope]:
        if not self.queue_path.exists():
            return []
        bundles: list[QueueEnvelope] = []
        for raw_line in self.queue_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("protocol") == "research-capture-queue/v2":
                bundles.append(QueuedV2Deposit.model_validate(payload))
            else:
                bundles.append(QueuedCaptureBundle.model_validate(payload))
        return bundles

    def flush(self, backend: RegistryBackend) -> QueueFlushResult:
        with self._lock():
            pending = self._list_pending_unlocked()
            remaining: list[QueueEnvelope] = []
            result = QueueFlushResult()
            status = backend.backend_status()
            for bundle in pending:
                if not self._matches_backend(bundle, status):
                    remaining.append(bundle)
                    continue
                try:
                    if isinstance(bundle, QueuedV2Deposit):
                        replay_result = self._replay_v2(bundle)
                    else:
                        replay_result = self._replay_bundle(backend, bundle)
                    result.flushed_queue_ids.append(bundle.queue_id)
                    if replay_result["report_id"] is not None:
                        result.stored_report_ids.append(replay_result["report_id"])
                except Exception as exc:
                    bundle.retry_count += 1
                    bundle.last_error = str(exc)
                    bundle.last_attempted_at = utc_now()
                    remaining.append(bundle)
                    result.failed_queue_ids.append(bundle.queue_id)
            self._write_all_unlocked(remaining)
            return result

    def _write_all(self, bundles: list[QueueEnvelope]) -> None:
        with self._lock():
            self._write_all_unlocked(bundles)

    def _write_all_unlocked(self, bundles: list[QueueEnvelope]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.queue_path.name}.",
            suffix=".tmp",
            dir=self.queue_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for bundle in bundles:
                    handle.write(bundle.model_dump_json(exclude_none=True))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.queue_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_path = self.queue_path.with_name(self.queue_path.name + ".lock")
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":  # pragma: no cover - Windows-only path
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                return
            lock_path.chmod(0o600)
            try:
                import fcntl
            except ImportError:  # pragma: no cover
                yield
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _matches_backend(self, bundle: QueueEnvelope, status: BackendStatus) -> bool:
        if bundle.backend_url and status.url and bundle.backend_url.rstrip("/") != status.url.rstrip("/"):
            return False
        if bundle.namespace_kind != status.namespace_kind:
            return False
        return bundle.namespace_id == status.namespace_id

    def _replay_v2(
        self, bundle: QueuedV2Deposit
    ) -> dict[str, str | None]:
        if self.deposit_service is None:
            raise RuntimeError(
                "v2 queue replay requires a local atomic deposit service"
            )
        receipt: ResearchDepositResult = self.deposit_service.deposit(
            bundle.deposit
        )
        return {
            "question_id": receipt.records.question_id,
            "session_id": receipt.records.run_id,
            "report_id": receipt.records.report_id,
        }

    def _replay_bundle(self, backend: RegistryBackend, bundle: QueuedCaptureBundle) -> dict[str, str]:
        question = self._ensure_question(backend, bundle)
        session = self._ensure_session(backend, bundle, question.id)
        annotation_map: dict[str, str] = {}
        for annotation in bundle.annotations:
            annotation_map[annotation.temp_id] = self._ensure_excerpt(
                backend,
                bundle,
                question_id=question.id,
                session_id=session.id,
                annotation=annotation,
            )
        finding_map: dict[str, str] = {}
        for finding in bundle.findings:
            excerpt_ids = [annotation_map[temp_id] for temp_id in finding.annotation_temp_ids]
            finding_map[finding.temp_id] = self._ensure_claim(
                backend,
                bundle,
                question_id=question.id,
                session_id=session.id,
                finding=finding,
                excerpt_ids=excerpt_ids,
            )
        finding_ids = [finding_map[temp_id] for temp_id in bundle.report.finding_temp_ids]
        report_id = self._ensure_report(
            backend,
            bundle,
            question_id=question.id,
            session_id=session.id,
            report=bundle.report,
            claim_ids=finding_ids,
        )
        backend.set_question_status(question.id, "answered")
        return {"question_id": question.id, "session_id": session.id, "report_id": report_id}

    def _ensure_question(self, backend: RegistryBackend, bundle: QueuedCaptureBundle):
        prompt = bundle.prompt.strip() or bundle.report.question.strip() or bundle.run.question
        focal_label = (bundle.report.subject or bundle.normalized_topic or "research").strip() or "research"
        return backend.create_question(
            QuestionCreate(
                prompt=prompt,
                focus=FocusTuple(label=focal_label),
                visibility=bundle.run.visibility,
                author_type=bundle.run.author_type,
                namespace_kind=bundle.namespace_kind,
                namespace_id=bundle.namespace_id,
                dedupe_key=f"{bundle.queue_id}:question",
            )
        )

    def _ensure_session(self, backend: RegistryBackend, bundle: QueuedCaptureBundle, question_id: str):
        notes = bundle.run.notes.strip() if bundle.run.notes else ""
        notes = f"{notes}\ncapture_queue_id={bundle.queue_id}".strip()
        return backend.create_session(
            ResearchSessionCreate(
                question_id=question_id,
                prompt=bundle.prompt.strip() or bundle.report.question.strip() or bundle.run.question,
                model_name=bundle.run.model_name,
                model_version=bundle.run.model_version,
                notes=notes,
                mode="synthesis",
                ttl_days=bundle.run.freshness_ttl_days,
                source_signals=[f"capture-queue:{bundle.queue_id}"],
                visibility=bundle.run.visibility,
                author_type=bundle.run.author_type,
                namespace_kind=bundle.namespace_kind,
                namespace_id=bundle.namespace_id,
                dedupe_key=f"{bundle.queue_id}:session",
            )
        )

    def _ensure_excerpt(
        self,
        backend: RegistryBackend,
        bundle: QueuedCaptureBundle,
        *,
        question_id: str,
        session_id: str,
        annotation: QueuedAnnotation,
    ) -> str:
        tags = list(annotation.tags)
        if annotation.parent_annotation_temp_id:
            tags.append(f"parent_annotation_temp_id:{annotation.parent_annotation_temp_id}")
        created_source = backend.create_source(
            annotation.source.model_copy(
                update={
                    "namespace_kind": bundle.namespace_kind,
                    "namespace_id": bundle.namespace_id,
                    "dedupe_key": f"source:{bundle.namespace_kind}:{bundle.namespace_id}:{annotation.source.locator}",
                }
            )
        )
        created = backend.create_excerpt(
            ExcerptCreate(
                source_id=created_source.id,
                question_id=question_id,
                session_id=session_id,
                focal_label=annotation.subject,
                note=annotation.note,
                selector=annotation.selector,
                quote_text=annotation.quote_text or annotation.selector.exact or annotation.note,
                confidence=annotation.confidence,
                tags=tags,
                visibility=annotation.visibility,
                author_type=annotation.author_type,
                model_name=annotation.model_name,
                model_version=annotation.model_version,
                namespace_kind=bundle.namespace_kind,
                namespace_id=bundle.namespace_id,
                dedupe_key=f"{bundle.queue_id}:excerpt:{annotation.temp_id}",
            )
        )
        return created.id

    def _ensure_claim(
        self,
        backend: RegistryBackend,
        bundle: QueuedCaptureBundle,
        *,
        question_id: str,
        session_id: str,
        finding: QueuedFinding,
        excerpt_ids: list[str],
    ) -> str:
        created = backend.create_claim(
            ClaimCreate(
                question_id=question_id,
                session_id=session_id,
                title=finding.title,
                focal_label=finding.subject,
                statement=finding.claim,
                excerpt_ids=excerpt_ids,
                visibility=finding.visibility,
                author_type=finding.author_type,
                model_name=finding.model_name,
                model_version=finding.model_version,
                confidence=finding.confidence if finding.confidence is not None else 0.7,
                namespace_kind=bundle.namespace_kind,
                namespace_id=bundle.namespace_id,
                dedupe_key=f"{bundle.queue_id}:claim:{finding.temp_id}",
            )
        )
        return created.id

    def _ensure_report(
        self,
        backend: RegistryBackend,
        bundle: QueuedCaptureBundle,
        *,
        question_id: str,
        session_id: str,
        report: QueuedReport,
        claim_ids: list[str],
    ) -> str:
        created = backend.create_report(
            ReportCreate(
                question_id=question_id,
                session_id=session_id,
                title=report.question or bundle.prompt,
                focal_label=report.subject,
                summary_md=report.summary_md,
                guidance=GuidancePayload(),
                claim_ids=claim_ids,
                visibility=report.visibility,
                author_type=report.author_type,
                model_name=report.model_name,
                model_version=report.model_version,
                namespace_kind=bundle.namespace_kind,
                namespace_id=bundle.namespace_id,
                dedupe_key=f"{bundle.queue_id}:report",
            )
        )
        return created.id
