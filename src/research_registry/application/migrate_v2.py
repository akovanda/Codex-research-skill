from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..migration_runner import MigrationRunner
from ..persistence.repositories import (
    V2BackfillRepository,
    V2_MIGRATION_ID,
)
from ..retrieval.projection import rebuild_search_documents
from ..service import RegistryService


BACKFILL_PHASES = (
    "source_versions",
    "evidence_spans",
    "claim_revisions",
    "claim_evidence",
    "claim_pointers",
    "report_state",
)
V2_SCHEMA_TARGET = "0006_v2_legacy_projection_identity"


class InjectedBackfillInterruption(RuntimeError):
    """Test-only interruption raised before the selected batch commits."""


class BackfillResumeRequired(RuntimeError):
    """Raised when an incomplete checkpoint exists without explicit resume."""


@dataclass(frozen=True)
class BackfillPhaseResult:
    phase: str
    status: str
    processed_count: int
    warning_count: int
    error_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "processed_count": self.processed_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
        }


@dataclass(frozen=True)
class BackfillResult:
    database_kind: str
    status: str
    migration_id: str
    processed_count: int
    warning_count: int
    error_count: int
    phases: tuple[BackfillPhaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_kind": self.database_kind,
            "status": self.status,
            "migration_id": self.migration_id,
            "processed_count": self.processed_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "phases": [phase.to_dict() for phase in self.phases],
        }


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_v2_backfill(
    database: str | Path,
    *,
    batch_size: int = 500,
    resume: bool = False,
    interrupt_after_batches: int | None = None,
) -> BackfillResult:
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    if interrupt_after_batches is not None and interrupt_after_batches < 1:
        raise ValueError("interrupt_after_batches must be positive")

    service = RegistryService(database)
    with service.connect() as conn:
        migration_state = MigrationRunner(service).verify(
            conn,
            target=V2_SCHEMA_TARGET,
        )
    if migration_state.pending_ids:
        raise RuntimeError(
            "v2 schema migrations are not applied; run research-registry migrate first"
        )
    with service.connect() as conn:
        repository = V2BackfillRepository(conn)
        repository.adopt_authoritative_projection_identities()
        repository.initialize_progress(BACKFILL_PHASES, updated_at=utc_now_text())
        progress = repository.progress()
        all_completed = bool(progress) and all(
            row["status"] == "completed" for row in progress
        )
        started = any(
            row["status"] != "pending" or int(row["processed_count"]) > 0
            for row in progress
        )
    if all_completed:
        # A completed migration may be rerun safely during the v1 compatibility
        # window. Rescanning also captures v1 rows written after the prior run.
        with service.connect() as conn:
            V2BackfillRepository(conn).reset_completed_progress(
                updated_at=utc_now_text()
            )
        started = False
    if started and not resume:
        raise BackfillResumeRequired(
            "v2 data backfill has an incomplete checkpoint; rerun with --resume"
        )

    attempted_batches = 0
    for phase in BACKFILL_PHASES:
        while True:
            with service.connect() as conn:
                repository = V2BackfillRepository(conn)
                progress_row = repository.phase_progress(phase)
                if progress_row["status"] == "completed":
                    break
                rows = repository.fetch_batch(
                    phase,
                    last_legacy_id=progress_row["last_legacy_id"],
                    last_related_id=progress_row["last_related_id"],
                    batch_size=batch_size,
                )
                if not rows:
                    repository.update_progress(
                        phase,
                        last_legacy_id=progress_row["last_legacy_id"],
                        last_related_id=progress_row["last_related_id"],
                        processed_count=0,
                        warning_count=0,
                        error_count=0,
                        status="completed",
                        updated_at=utc_now_text(),
                    )
                    break

                warning_count = 0
                error_count = 0
                for row in rows:
                    row_warnings, row_errors = repository.process_row(
                        phase, row
                    )
                    warning_count += row_warnings
                    error_count += row_errors
                last_legacy_id, last_related_id = _checkpoint_for_rows(
                    phase, rows
                )
                repository.update_progress(
                    phase,
                    last_legacy_id=last_legacy_id,
                    last_related_id=last_related_id,
                    processed_count=len(rows),
                    warning_count=warning_count,
                    error_count=error_count,
                    status="running",
                    updated_at=utc_now_text(),
                )
                attempted_batches += 1
                if attempted_batches == interrupt_after_batches:
                    raise InjectedBackfillInterruption(
                        "injected v2 backfill interruption"
                    )

    with service.connect() as conn:
        repository = V2BackfillRepository(conn)
        repository.refresh_projection_mirrors()
        if "search_documents" in service._list_tables(conn):
            rebuild_search_documents(conn)
    return _result(service)


def _checkpoint_for_rows(
    phase: str, rows: list[Any]
) -> tuple[str, str | None]:
    last = rows[-1]
    if phase == "claim_evidence":
        return last["claim_id"], last["excerpt_id"]
    return last["id"], None


def _result(service: RegistryService) -> BackfillResult:
    with service.connect() as conn:
        repository = V2BackfillRepository(conn)
        progress = repository.progress()
        warning_count, error_count = repository.totals()
    phases = tuple(
        BackfillPhaseResult(
            phase=row["phase"],
            status=row["status"],
            processed_count=int(row["processed_count"]),
            warning_count=int(row["warning_count"]),
            error_count=int(row["error_count"]),
        )
        for row in sorted(
            progress,
            key=lambda row: BACKFILL_PHASES.index(row["phase"]),
        )
    )
    return BackfillResult(
        database_kind=service.database.kind,
        status="completed" if error_count == 0 else "incomplete",
        migration_id=V2_MIGRATION_ID,
        processed_count=sum(phase.processed_count for phase in phases),
        warning_count=warning_count,
        error_count=error_count,
        phases=phases,
    )
