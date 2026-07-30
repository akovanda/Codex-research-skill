from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .config import load_settings
from .data_audit import connect_database_read_only
from .db import DbConnection
from .migration_runner import MigrationResult, MigrationRunner
from .service import RegistryService


def run_migration(
    database: str | Path,
    *,
    operation: str = "migrate",
    target: str | None = None,
) -> MigrationResult:
    service = RegistryService(database)
    runner = MigrationRunner(service)
    if operation in {"plan", "verify"}:
        with _migration_read_connection(service) as conn:
            if operation == "plan":
                return runner.plan(conn, target=target)
            return runner.verify(conn, target=target)
    with service.connect() as conn:
        if operation == "dry_run":
            return runner.migrate(conn, target=target, dry_run=True)
        if operation == "migrate":
            return runner.migrate(conn, target=target)
    raise ValueError(f"unsupported migration operation: {operation}")


@contextmanager
def _migration_read_connection(
    service: RegistryService,
) -> Iterator[DbConnection]:
    target = service.database
    if (
        target.kind == "sqlite"
        and target.sqlite_path is not None
        and not target.sqlite_path.exists()
    ):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        connection = DbConnection(target, raw)
        try:
            yield connection
        finally:
            connection.close()
        return
    with connect_database_read_only(target) as connection:
        yield connection


def format_migration_result(
    result: MigrationResult, *, json_output: bool
) -> str:
    if json_output:
        return json.dumps(result.to_dict(), sort_keys=True)
    counts = {
        "applied": len(result.applied_ids),
        "adopted": len(result.adopted_ids),
        "pending": len(result.pending_ids),
        "verified": len(result.verified_ids),
        "skipped_non_transactional": len(
            result.skipped_non_transactional_ids
        ),
    }
    count_text = " ".join(
        f"{name}={count}" for name, count in counts.items()
    )
    target = result.target or "latest"
    return (
        f"migration {result.operation}: status={result.status} "
        f"database_kind={result.database_kind} target={target} {count_text}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-registry-migrate",
        description="Plan, apply, dry-run, or verify packaged database migrations.",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target", default=None, metavar="MIGRATION_ID")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    operation = (
        "plan"
        if args.plan
        else "verify"
        if args.verify
        else "dry_run"
        if args.dry_run
        else "migrate"
    )
    database = args.database or load_settings().database_url
    result = run_migration(
        database,
        operation=operation,
        target=args.target,
    )
    print(format_migration_result(result, json_output=args.json))


if __name__ == "__main__":
    main()
