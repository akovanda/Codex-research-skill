from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator, Mapping

from .db import DbConnection, split_sql_script


LEGACY_SCHEMA_VERSION = 4
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
_DIALECTS = ("postgres", "sqlite")
_BUNDLE_COMPONENTS = ("common.sql", "manifest.json", "postgres.sql", "sqlite.sql")
_MANIFEST_FIELDS = {"description", "migration_id", "transactional"}
_SAFE_MIGRATION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_POSTGRES_LOCK_KEY = int.from_bytes(
    sha256(b"research-registry:schema-migrations").digest()[:8],
    byteorder="big",
    signed=True,
)


@dataclass(frozen=True)
class SqlMigration:
    migration_id: str
    checksum_sha256: str
    sql: str
    dialect_sql: Mapping[str, str] = field(default_factory=dict)
    component_files: tuple[str, ...] = ()
    transactional_dialects: frozenset[str] = field(
        default_factory=lambda: frozenset(_DIALECTS)
    )
    source_kind: str = "flat"

    def sql_for(self, dialect: str) -> str:
        _require_dialect(dialect)
        selected = self.dialect_sql.get(dialect)
        if selected is None:
            return self.sql
        return "\n\n".join(part for part in (self.sql, selected) if part)

    def selected_files(self, dialect: str) -> tuple[str, ...]:
        _require_dialect(dialect)
        if self.source_kind == "flat":
            return self.component_files
        return ("common.sql", f"{dialect}.sql")

    def is_transactional(self, dialect: str) -> bool:
        _require_dialect(dialect)
        return dialect in self.transactional_dialects


@dataclass(frozen=True)
class MigrationStep:
    migration_id: str
    checksum_sha256: str
    state: str
    source_kind: str
    component_files: tuple[str, ...]
    selected_files: tuple[str, ...]
    transactional: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "checksum_sha256": self.checksum_sha256,
            "state": self.state,
            "source_kind": self.source_kind,
            "component_files": list(self.component_files),
            "selected_files": list(self.selected_files),
            "transactional": self.transactional,
        }


@dataclass(frozen=True)
class MigrationResult:
    operation: str
    database_kind: str
    status: str
    target: str | None
    migrations: tuple[MigrationStep, ...]
    applied_ids: tuple[str, ...] = ()
    adopted_ids: tuple[str, ...] = ()
    pending_ids: tuple[str, ...] = ()
    verified_ids: tuple[str, ...] = ()
    skipped_non_transactional_ids: tuple[str, ...] = ()
    skipped_non_transactional_files: tuple[str, ...] = ()
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "database_kind": self.database_kind,
            "status": self.status,
            "target": self.target,
            "migrations": [migration.to_dict() for migration in self.migrations],
            "applied_ids": list(self.applied_ids),
            "adopted_ids": list(self.adopted_ids),
            "pending_ids": list(self.pending_ids),
            "verified_ids": list(self.verified_ids),
            "skipped_non_transactional_ids": list(
                self.skipped_non_transactional_ids
            ),
            "skipped_non_transactional_files": list(
                self.skipped_non_transactional_files
            ),
            "rolled_back": self.rolled_back,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_sql_migrations(migrations_path: Any | None = None) -> list[SqlMigration]:
    """Load packaged flat files and logical migration bundles deterministically."""
    root = migrations_path or files("research_registry.migrations")
    migrations: list[SqlMigration] = []
    seen_ids: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith((".", "__")):
            continue
        if path.is_file() and Path(path.name).suffix == ".sql":
            migration = _load_flat_migration(path)
        elif path.is_dir():
            migration = _load_migration_bundle(path)
        else:
            continue
        if migration.migration_id in seen_ids:
            raise RuntimeError(f"duplicate migration id: {migration.migration_id}")
        seen_ids.add(migration.migration_id)
        migrations.append(migration)
    migrations.sort(key=lambda migration: migration.migration_id)
    return migrations


def _load_flat_migration(path: Any) -> SqlMigration:
    sql = path.read_text(encoding="utf-8").strip()
    if not sql:
        raise RuntimeError(f"empty migration file: {path.name}")
    migration_id = Path(path.name).stem
    _validate_migration_id(migration_id)
    # This exact algorithm predates dialect bundles and is intentionally frozen.
    checksum = sha256(sql.encode("utf-8")).hexdigest()
    return SqlMigration(
        migration_id=migration_id,
        checksum_sha256=checksum,
        sql=sql,
        component_files=(path.name,),
    )


def _load_migration_bundle(path: Any) -> SqlMigration:
    migration_id = path.name
    _validate_migration_id(migration_id)
    missing = [
        name for name in _BUNDLE_COMPONENTS if not path.joinpath(name).is_file()
    ]
    if missing:
        raise RuntimeError(
            f"incomplete migration bundle: {migration_id}; missing {', '.join(missing)}"
        )

    components = {
        name: path.joinpath(name).read_bytes() for name in _BUNDLE_COMPONENTS
    }
    manifest = _parse_manifest(migration_id, components["manifest.json"])
    common_sql = _decode_sql_component(
        migration_id, "common.sql", components["common.sql"]
    )
    dialect_sql = {
        dialect: _decode_sql_component(
            migration_id, f"{dialect}.sql", components[f"{dialect}.sql"]
        )
        for dialect in _DIALECTS
    }
    transactional = _transactional_dialects(migration_id, manifest)
    return SqlMigration(
        migration_id=migration_id,
        checksum_sha256=_bundle_checksum(components),
        sql=common_sql,
        dialect_sql=dialect_sql,
        component_files=_BUNDLE_COMPONENTS,
        transactional_dialects=transactional,
        source_kind="bundle",
    )


def _parse_manifest(migration_id: str, raw_manifest: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid migration manifest: {migration_id}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"invalid migration manifest: {migration_id}")
    unknown_fields = sorted(set(manifest) - _MANIFEST_FIELDS)
    if unknown_fields:
        raise RuntimeError(
            f"invalid migration manifest: {migration_id}; "
            f"unknown fields {', '.join(unknown_fields)}"
        )
    missing_fields = sorted(
        {"migration_id", "transactional"} - set(manifest)
    )
    if missing_fields:
        raise RuntimeError(
            f"invalid migration manifest: {migration_id}; "
            f"missing fields {', '.join(missing_fields)}"
        )
    if manifest.get("migration_id") != migration_id:
        raise RuntimeError(
            f"migration manifest id mismatch: {migration_id}"
        )
    description = manifest.get("description")
    if description is not None and (
        not isinstance(description, str) or len(description) > 512
    ):
        raise RuntimeError(
            f"invalid migration manifest description: {migration_id}"
        )
    return manifest


def _transactional_dialects(
    migration_id: str, manifest: Mapping[str, Any]
) -> frozenset[str]:
    value = manifest.get("transactional", True)
    if isinstance(value, bool):
        return frozenset(_DIALECTS if value else ())
    if not isinstance(value, dict) or set(value) != set(_DIALECTS):
        raise RuntimeError(
            f"invalid migration manifest transactional field: {migration_id}"
        )
    if any(not isinstance(value[dialect], bool) for dialect in _DIALECTS):
        raise RuntimeError(
            f"invalid migration manifest transactional field: {migration_id}"
        )
    return frozenset(
        dialect for dialect in _DIALECTS if value[dialect]
    )


def _decode_sql_component(
    migration_id: str, component_name: str, content: bytes
) -> str:
    try:
        return content.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"invalid UTF-8 migration component: {migration_id}/{component_name}"
        ) from exc


def _bundle_checksum(components: Mapping[str, bytes]) -> str:
    digest = sha256()
    digest.update(b"research-registry-logical-migration-v1\0")
    for name in sorted(components):
        encoded_name = name.encode("utf-8")
        content = (
            components[name]
            .decode("utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode("utf-8")
        )
        digest.update(len(encoded_name).to_bytes(4, byteorder="big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return digest.hexdigest()


def _validate_migration_id(migration_id: str) -> None:
    if not _SAFE_MIGRATION_ID.fullmatch(migration_id):
        raise RuntimeError(f"invalid migration id: {migration_id}")


def _require_dialect(dialect: str) -> None:
    if dialect not in _DIALECTS:
        raise ValueError(f"unsupported migration dialect: {dialect}")


class MigrationRunner:
    def __init__(self, service: Any, *, migrations_path: Any | None = None):
        self.service = service
        self.migrations = load_sql_migrations(migrations_path)

    def plan(
        self, conn: DbConnection, *, target: str | None = None
    ) -> MigrationResult:
        selected = self._select_migrations(target)
        applied = self._read_and_validate_applied(conn)
        steps = self._steps(selected, applied)
        return MigrationResult(
            operation="plan",
            database_kind=conn.target.kind,
            status="planned",
            target=target,
            migrations=steps,
            pending_ids=tuple(
                migration.migration_id
                for migration in selected
                if migration.migration_id not in applied
            ),
            verified_ids=tuple(
                migration.migration_id
                for migration in selected
                if migration.migration_id in applied
            ),
        )

    def verify(
        self, conn: DbConnection, *, target: str | None = None
    ) -> MigrationResult:
        selected = self._select_migrations(target)
        applied = self._read_and_validate_applied(conn)
        steps = self._steps(selected, applied)
        return MigrationResult(
            operation="verify",
            database_kind=conn.target.kind,
            status="verified",
            target=target,
            migrations=steps,
            pending_ids=tuple(
                migration.migration_id
                for migration in selected
                if migration.migration_id not in applied
            ),
            verified_ids=tuple(
                migration.migration_id
                for migration in selected
                if migration.migration_id in applied
            ),
        )

    def migrate(
        self,
        conn: DbConnection,
        *,
        target: str | None = None,
        dry_run: bool = False,
    ) -> MigrationResult:
        selected = self._select_migrations(target)
        dialect = conn.target.kind
        with self._migration_lock(conn):
            try:
                self._begin_migration_transaction(conn)
                self._ensure_schema_migrations_table(conn)
                applied_before = self._applied_migrations(conn)
                self._validate_applied(applied_before, conn)
                if not applied_before:
                    self._bootstrap_schema(conn)
                applied = self._applied_migrations(conn)
                self._validate_applied(applied, conn)
                adopted_ids = tuple(sorted(set(applied) - set(applied_before)))
                pending = [
                    migration
                    for migration in selected
                    if migration.migration_id not in applied
                ]
                non_transactional = [
                    migration
                    for migration in pending
                    if not migration.is_transactional(dialect)
                ]
                if non_transactional and not dry_run:
                    raise RuntimeError(
                        "non-transactional migration requires explicit "
                        f"operator recovery: {non_transactional[0].migration_id}"
                    )
                skipped = tuple(
                    migration.migration_id
                    for migration in non_transactional
                )
                skipped_files = tuple(
                    f"{migration.migration_id}/{filename}"
                    for migration in non_transactional
                    for filename in migration.selected_files(dialect)
                )
                executable = [
                    migration
                    for migration in pending
                    if migration.migration_id not in skipped
                ]
                for migration in executable:
                    self._apply_migration(conn, migration)
                    self._record_migration(conn, migration)

                if dry_run:
                    conn.rollback()
                    return MigrationResult(
                        operation="dry_run",
                        database_kind=dialect,
                        status="rolled_back",
                        target=target,
                        migrations=self._steps(selected, applied),
                        adopted_ids=adopted_ids,
                        pending_ids=tuple(
                            migration.migration_id for migration in pending
                        ),
                        verified_ids=tuple(
                            migration.migration_id
                            for migration in selected
                            if migration.migration_id in applied
                        ),
                        skipped_non_transactional_ids=skipped,
                        skipped_non_transactional_files=skipped_files,
                        rolled_back=True,
                    )

                conn.commit()
                final_applied = dict(applied)
                final_applied.update(
                    (migration.migration_id, migration.checksum_sha256)
                    for migration in executable
                )
                return MigrationResult(
                    operation="migrate",
                    database_kind=dialect,
                    status="applied" if executable or adopted_ids else "current",
                    target=target,
                    migrations=self._steps(selected, final_applied),
                    applied_ids=tuple(
                        migration.migration_id for migration in executable
                    ),
                    adopted_ids=adopted_ids,
                    pending_ids=tuple(
                        migration.migration_id
                        for migration in selected
                        if migration.migration_id not in final_applied
                    ),
                    verified_ids=tuple(
                        migration.migration_id
                        for migration in selected
                        if migration.migration_id in applied
                    ),
                )
            except Exception:
                conn.rollback()
                raise

    def _select_migrations(self, target: str | None) -> list[SqlMigration]:
        if target is None:
            return list(self.migrations)
        ids = [migration.migration_id for migration in self.migrations]
        if target not in ids:
            raise RuntimeError(f"unknown migration target: {target}")
        return self.migrations[: ids.index(target) + 1]

    def _steps(
        self,
        migrations: list[SqlMigration],
        applied: Mapping[str, str],
    ) -> tuple[MigrationStep, ...]:
        dialect = self.service.database.kind
        return tuple(
            MigrationStep(
                migration_id=migration.migration_id,
                checksum_sha256=migration.checksum_sha256,
                state=(
                    "applied"
                    if migration.migration_id in applied
                    else "pending"
                ),
                source_kind=migration.source_kind,
                component_files=migration.component_files,
                selected_files=migration.selected_files(dialect),
                transactional=migration.is_transactional(dialect),
            )
            for migration in migrations
        )

    def _apply_migration(
        self, conn: DbConnection, migration: SqlMigration
    ) -> None:
        for statement in split_sql_script(
            migration.sql_for(conn.target.kind),
            dialect=conn.target.kind,
        ):
            conn.execute(statement)

    def _bootstrap_schema(self, conn: DbConnection) -> None:
        tables = self.service._list_tables(conn)
        if not tables or tables == {"schema_migrations"}:
            return

        if "schema_meta" in tables:
            row = conn.execute(
                "SELECT version FROM schema_meta LIMIT 1"
            ).fetchone()
            version = row["version"] if row else None
            if version is not None and version < LEGACY_SCHEMA_VERSION:
                self.service._migrate_schema_legacy(conn, version)
                self.service._create_schema_legacy(conn)
            # The embedded legacy schema represents only the frozen flat migrations.
            for migration in self.migrations:
                if migration.source_kind == "flat":
                    self._record_migration(conn, migration)
            return

        existing_managed_tables = tables & MANAGED_TABLES
        if existing_managed_tables:
            raise RuntimeError(
                "database contains managed tables but no schema history; "
                "manual migration adoption required"
            )

    def _read_and_validate_applied(
        self, conn: DbConnection
    ) -> dict[str, str]:
        tables = self.service._list_tables(conn)
        if "schema_migrations" not in tables:
            if "schema_meta" not in tables and tables & MANAGED_TABLES:
                raise RuntimeError(
                    "database contains managed tables but no schema history; "
                    "manual migration adoption required"
                )
            return {}
        applied = self._applied_migrations(conn)
        self._validate_applied(applied, conn)
        return applied

    def _validate_applied(
        self, applied: Mapping[str, str], conn: DbConnection
    ) -> None:
        tables = self.service._list_tables(conn)
        if not applied and "schema_meta" not in tables and tables & MANAGED_TABLES:
            raise RuntimeError(
                "database contains managed tables but no schema history; "
                "manual migration adoption required"
            )
        packaged = {
            migration.migration_id: migration for migration in self.migrations
        }
        for migration_id, recorded_checksum in applied.items():
            migration = packaged.get(migration_id)
            if migration is None:
                raise RuntimeError(
                    f"unknown applied migration: {migration_id}"
                )
            if recorded_checksum != migration.checksum_sha256:
                raise RuntimeError(
                    f"migration checksum mismatch: {migration_id}"
                )

        seen_pending = False
        for migration in self.migrations:
            is_applied = migration.migration_id in applied
            if not is_applied:
                seen_pending = True
            elif seen_pending:
                raise RuntimeError(
                    f"migration history has an out-of-order entry: "
                    f"{migration.migration_id}"
                )

        if "0001_initial" in applied:
            missing_tables = MANAGED_TABLES - tables
            if missing_tables:
                raise RuntimeError(
                    "managed schema invariant failed; missing tables: "
                    + ", ".join(sorted(missing_tables))
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
            "SELECT migration_id, checksum_sha256 "
            "FROM schema_migrations ORDER BY migration_id ASC"
        ).fetchall()
        return {
            row["migration_id"]: row["checksum_sha256"] for row in rows
        }

    def _record_migration(
        self, conn: DbConnection, migration: SqlMigration
    ) -> None:
        conn.execute(
            """
            INSERT INTO schema_migrations (
                migration_id, checksum_sha256, applied_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO NOTHING
            """,
            (
                migration.migration_id,
                migration.checksum_sha256,
                utc_now().isoformat(),
            ),
        )

    def _begin_migration_transaction(self, conn: DbConnection) -> None:
        if conn.target.kind == "sqlite":
            conn.execute("BEGIN EXCLUSIVE")

    @contextmanager
    def _migration_lock(self, conn: DbConnection) -> Iterator[None]:
        if conn.target.kind == "postgres":
            conn.execute(
                "SELECT pg_advisory_lock(?)", (_POSTGRES_LOCK_KEY,)
            )
            try:
                yield
            finally:
                conn.execute(
                    "SELECT pg_advisory_unlock(?)", (_POSTGRES_LOCK_KEY,)
                )
            return

        assert conn.target.sqlite_path is not None
        lock_path = conn.target.sqlite_path.with_name(
            conn.target.sqlite_path.name + ".migration.lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":  # pragma: no cover - Windows-only lock path
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
            else:
                lock_path.chmod(0o600)
            try:
                import fcntl
            except ImportError:  # pragma: no cover - non-POSIX fallback
                yield
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
