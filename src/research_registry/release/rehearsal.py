from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns

from ..application.migrate_v2 import run_v2_backfill
from ..backup import backup_sqlite, restore_sqlite_backup
from ..migration_runner import MigrationRunner
from ..service import RegistryService


@dataclass(frozen=True)
class ReleaseRehearsalResult:
    fresh_install: bool
    upgrade: bool
    backup: bool
    restore: bool
    rollback: bool
    data_loss_count: int
    unresolved_migration_errors: int
    duration_ms: float
    upgraded_database: Path
    restored_database: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": "research-registry-release-rehearsal/v1",
            "fresh_install": self.fresh_install,
            "upgrade": self.upgrade,
            "backup": self.backup,
            "restore": self.restore,
            "rollback": self.rollback,
            "data_loss_count": self.data_loss_count,
            "unresolved_migration_errors": self.unresolved_migration_errors,
            "duration_ms": self.duration_ms,
            "upgraded_database": str(self.upgraded_database),
            "restored_database": str(self.restored_database),
        }


def rehearse_sqlite_upgrade(root: Path) -> ReleaseRehearsalResult:
    """Rehearse a copied v1 database; no configured operator data is touched."""
    root = root.expanduser().resolve()
    started = perf_counter_ns()
    root.mkdir(parents=True, exist_ok=True)
    fresh_database = root / "fresh.sqlite3"
    legacy_database = root / "legacy.sqlite3"
    backup_database = root / "pre-upgrade.backup.sqlite3"
    backup_manifest = root / "pre-upgrade.backup.manifest.json"
    restored_database = root / "rollback.sqlite3"
    for path in (
        fresh_database,
        legacy_database,
        backup_database,
        backup_manifest,
        restored_database,
    ):
        if path.exists():
            raise FileExistsError(path)

    fresh = RegistryService(fresh_database)
    fresh.initialize()
    with fresh.connect() as conn:
        fresh_install = not MigrationRunner(fresh).verify(conn).pending_ids

    legacy = RegistryService(legacy_database)
    with legacy.connect() as conn:
        MigrationRunner(legacy).migrate(conn, target="0001_initial")
        conn.execute(
            """
            INSERT INTO topics (
                id, label, slug, focus_json, namespace_kind, namespace_id,
                dedupe_key, created_at
            ) VALUES (
                'topic_rehearsal', 'Release rehearsal', 'release-rehearsal',
                '{}', 'user', 'local', 'release-rehearsal-topic',
                '2026-07-30T00:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, topic_id, prompt, normalized_prompt, focus_json, status,
                priority_score, visibility, author_type, namespace_kind,
                namespace_id, public_index_state, dedupe_key, human_reviewed,
                created_at
            ) VALUES (
                'q_release_rehearsal', 'topic_rehearsal',
                'Synthetic release rehearsal question',
                'synthetic release rehearsal question', '{}', 'open', 0,
                'private', 'human', 'user', 'local', 'private',
                'release-rehearsal-question', 1,
                '2026-07-30T00:00:00+00:00'
            )
            """
        )
    before_count = _question_count(legacy)
    backup_sqlite(
        legacy_database,
        backup_database,
        manifest_path=backup_manifest,
    )

    with legacy.connect() as conn:
        migration = MigrationRunner(legacy).migrate(conn)
    backfill = run_v2_backfill(
        legacy_database,
        batch_size=10,
        resume=True,
    )
    after_count = _question_count(legacy)
    data_loss_count = max(0, before_count - after_count)
    upgrade = not migration.pending_ids and backfill.status == "completed"

    restore_result = restore_sqlite_backup(
        backup_database,
        restored_database,
        manifest_path=backup_manifest,
        verify=True,
    )
    restored = RegistryService(restored_database)
    with restored.connect() as conn:
        tables = restored._list_tables(conn)
    rollback = (
        _question_count(restored) == before_count
        and "source_versions" not in tables
    )
    return ReleaseRehearsalResult(
        fresh_install=fresh_install,
        upgrade=upgrade,
        backup=True,
        restore=restore_result["verified"] is True,
        rollback=rollback,
        data_loss_count=data_loss_count,
        unresolved_migration_errors=backfill.error_count,
        duration_ms=round((perf_counter_ns() - started) / 1_000_000, 3),
        upgraded_database=legacy_database,
        restored_database=restored_database,
    )


def _question_count(service: RegistryService) -> int:
    with service.connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM questions"
            ).fetchone()["count"]
        )
