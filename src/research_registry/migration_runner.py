from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files

from .db import DbConnection


LEGACY_SCHEMA_VERSION = 3
MANAGED_TABLES = {
    "topics",
    "questions",
    "research_sessions",
    "sources",
    "excerpts",
    "claims",
    "claim_excerpts",
    "reports",
    "report_claims",
    "users",
    "organizations",
    "org_memberships",
    "api_keys",
    "audit_log",
}


@dataclass(frozen=True)
class SqlMigration:
    migration_id: str
    checksum_sha256: str
    sql: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_sql_migrations() -> list[SqlMigration]:
    package = files("research_registry.migrations")
    migrations: list[SqlMigration] = []
    for path in sorted(package.iterdir(), key=lambda item: item.name):
        if path.suffix != ".sql":
            continue
        sql = path.read_text(encoding="utf-8").strip()
        if not sql:
            continue
        migrations.append(
            SqlMigration(
                migration_id=path.stem,
                checksum_sha256=sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    return migrations


class MigrationRunner:
    def __init__(self, service):
        self.service = service
        self.migrations = load_sql_migrations()

    def migrate(self, conn: DbConnection) -> None:
        self._ensure_schema_migrations_table(conn)
        applied = self._applied_migrations(conn)
        if not applied:
            self._bootstrap_schema(conn)
            applied = self._applied_migrations(conn)
        for migration in self.migrations:
            recorded_checksum = applied.get(migration.migration_id)
            if recorded_checksum is not None:
                if recorded_checksum != migration.checksum_sha256:
                    raise RuntimeError(f"migration checksum mismatch: {migration.migration_id}")
                continue
            conn.executescript(migration.sql)
            self._record_migration(conn, migration)

    def _bootstrap_schema(self, conn: DbConnection) -> None:
        tables = self.service._list_tables(conn)
        if not tables or tables == {"schema_migrations"}:
            return

        if "schema_meta" in tables:
            row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            version = row["version"] if row else None
            if version is not None and version < LEGACY_SCHEMA_VERSION:
                self.service._migrate_schema_legacy(conn, version)
                self.service._create_schema_legacy(conn)
            for migration in self.migrations:
                self._record_migration(conn, migration)
            return

        existing_managed_tables = tables & MANAGED_TABLES
        if existing_managed_tables:
            raise RuntimeError(
                "database contains managed tables but no schema history; manual migration adoption required"
            )

    def _ensure_schema_migrations_table(self, conn: DbConnection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                checksum_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    def _applied_migrations(self, conn: DbConnection) -> dict[str, str]:
        rows = conn.execute(
            "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id ASC"
        ).fetchall()
        return {row["migration_id"]: row["checksum_sha256"] for row in rows}

    def _record_migration(self, conn: DbConnection, migration: SqlMigration) -> None:
        conn.execute(
            """
            INSERT INTO schema_migrations (migration_id, checksum_sha256, applied_at)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
                checksum_sha256 = excluded.checksum_sha256,
                applied_at = excluded.applied_at
            """,
            (migration.migration_id, migration.checksum_sha256, utc_now().isoformat()),
        )
