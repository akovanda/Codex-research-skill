from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path

from .local_manager import (
    diagnose_local_runtime,
    ensure_prerequisites,
    format_doctor,
    format_status,
    format_tokens,
    format_uninstall_result,
    install_local_runtime,
    local_runtime_status,
    local_runtime_tokens,
    repair_local_runtime,
    stop_local_runtime,
    uninstall_local_runtime,
)


def _package_version() -> str:
    try:
        return version("research-registry")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-registry",
        description="Manage the local Research Registry runtime and Codex integration.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    subparsers = parser.add_subparsers(dest="command")

    up = subparsers.add_parser("up", help="Install or update the managed localhost runtime.")
    up.add_argument("--port", type=int, default=None, help="Host port for the local registry.")
    up.add_argument("--image", default=None, help="Container image to run. Defaults to RESEARCH_REGISTRY_IMAGE or the release image.")
    up.add_argument(
        "--build-local-image",
        action="store_true",
        help="Build the runtime image from the current source checkout instead of pulling/using an existing image.",
    )
    up.add_argument("--skip-pull", action="store_true", help="Do not pull the configured image before starting.")
    up.add_argument("--skip-start", action="store_true", help="Write config and Compose files but do not start the stack.")
    up.add_argument("--skip-codex-config", action="store_true", help="Do not patch ~/.codex/config.toml.")
    up.add_argument("--skip-skill-install", action="store_true", help="Do not install the managed skills into ~/.codex/skills.")

    subparsers.add_parser("status", help="Show the current localhost runtime status.")
    subparsers.add_parser("doctor", help="Check Docker, runtime, Codex MCP config, image, and skills.")

    repair = subparsers.add_parser("repair", help="Repair managed config files, Codex MCP config, and skill links.")
    repair.add_argument("--skip-codex-config", action="store_true", help="Do not patch ~/.codex/config.toml.")
    repair.add_argument("--skip-skill-install", action="store_true", help="Do not install the managed skills into ~/.codex/skills.")

    subparsers.add_parser("down", help="Stop the localhost runtime.")
    subparsers.add_parser("token", help="Print the managed localhost admin token and API key.")

    uninstall = subparsers.add_parser("uninstall", help="Stop the runtime and remove the managed Codex integration.")
    uninstall.add_argument(
        "--restore-codex-backup",
        action="store_true",
        help="Restore ~/.codex/config.toml.research-registry.bak when present.",
    )
    uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help="Also remove managed local config/data directories and docker volumes.",
    )

    subparsers.add_parser("web", help="Run the web app directly from the current environment.")

    audit = subparsers.add_parser(
        "audit-data",
        help="Run a read-only, content-free v1 database audit.",
    )
    audit.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    audit.add_argument("--json-out", type=Path, default=None, help="Write the JSON audit to a new file.")
    audit.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Write the Markdown audit to a new file.",
    )

    backup = subparsers.add_parser(
        "backup",
        help="Create a verified SQLite backup or print a redacted Postgres backup plan.",
    )
    backup.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    backup.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New SQLite backup path or planned Postgres dump path.",
    )
    backup.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="New SQLite manifest path or optional Postgres plan output path.",
    )
    backup.add_argument(
        "--restore-database",
        default=None,
        help="Disposable Postgres restore URL used only for a redacted plan.",
    )

    restore = subparsers.add_parser("restore", help="Restore a SQLite backup to a new path.")
    restore.add_argument("--backup", type=Path, required=True, help="Verified SQLite backup artifact.")
    restore.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Backup manifest containing SHA-256 and inventory.",
    )
    restore.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="New restore destination; existing files are refused.",
    )
    restore.add_argument(
        "--verify",
        action="store_true",
        help="Verify backup SHA-256, integrity, counts, and hashes before restoring.",
    )

    migrate = subparsers.add_parser(
        "migrate",
        help="Plan, apply, dry-run, or verify packaged database migrations.",
    )
    migrate.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    migrate_mode = migrate.add_mutually_exclusive_group()
    migrate_mode.add_argument(
        "--plan",
        action="store_true",
        help="List applied and pending migrations without database writes.",
    )
    migrate_mode.add_argument(
        "--verify",
        action="store_true",
        help="Verify applied checksums and managed schema invariants without writes.",
    )
    migrate_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply transactional migrations and roll back; report skipped non-transactional bundles.",
    )
    migrate.add_argument(
        "--target",
        default=None,
        metavar="MIGRATION_ID",
        help="Stop the selected operation at this packaged migration.",
    )
    migrate.add_argument(
        "--json",
        action="store_true",
        help="Emit the structured migration result as JSON.",
    )

    migrate_v2_data = subparsers.add_parser(
        "migrate-v2-data",
        help="Backfill additive v2 evidence records from v1 data in batches.",
    )
    migrate_v2_data.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    migrate_v2_data.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Records per transaction (1-10000).",
    )
    migrate_v2_data.add_argument(
        "--resume",
        action="store_true",
        help="Resume an incomplete backfill from its durable checkpoints.",
    )
    migrate_v2_data.add_argument(
        "--json",
        action="store_true",
        help="Emit content-free structured counts and phase states as JSON.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "up":
        ensure_prerequisites()
        install_local_runtime(
            port=args.port,
            image_tag=args.image,
            build_image=args.build_local_image,
            pull_image=not args.skip_pull,
            start_stack=not args.skip_start,
            configure_codex=not args.skip_codex_config,
            install_skills=not args.skip_skill_install,
        )
        print(format_status(local_runtime_status()))
        return

    if args.command == "status":
        print(format_status(local_runtime_status()))
        return

    if args.command == "doctor":
        print(format_doctor(diagnose_local_runtime()))
        return

    if args.command == "repair":
        repair_local_runtime(
            configure_codex=not args.skip_codex_config,
            install_skills=not args.skip_skill_install,
        )
        print(format_status(local_runtime_status()))
        return

    if args.command == "down":
        stop_local_runtime()
        print(format_status(local_runtime_status()))
        return

    if args.command == "token":
        print(format_tokens(local_runtime_tokens()))
        return

    if args.command == "uninstall":
        result = uninstall_local_runtime(
            restore_codex_backup=args.restore_codex_backup,
            purge_data=args.purge_data,
        )
        print(format_uninstall_result(result))
        print()
        print(format_status(local_runtime_status()))
        return

    if args.command == "web":
        from .web import main as web_main

        web_main()
        return

    if args.command == "audit-data":
        from .config import load_settings
        from .data_audit import audit_database, render_audit_markdown

        database = args.database or load_settings().database_url
        report = audit_database(database)
        wrote_report = False
        if args.json_out is not None:
            _write_new_text(args.json_out, json.dumps(report, indent=2, sort_keys=True) + "\n")
            wrote_report = True
        if args.markdown_out is not None:
            _write_new_text(args.markdown_out, render_audit_markdown(report))
            wrote_report = True
        if wrote_report:
            print("Audit reports written.")
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
        return

    if args.command == "backup":
        from .backup import backup_sqlite, plan_postgres_backup
        from .config import load_settings
        from .db import resolve_database_target

        database = args.database or load_settings().database_url
        target = resolve_database_target(database)
        if target.kind == "sqlite":
            manifest_path = args.manifest or args.output.with_suffix(args.output.suffix + ".manifest.json")
            manifest = backup_sqlite(target, args.output, manifest_path=manifest_path)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "database_kind": "sqlite",
                        "sha256": manifest["artifacts"][0]["sha256"],
                    },
                    sort_keys=True,
                )
            )
        else:
            plan = plan_postgres_backup(
                target.url,
                dump_path=args.output,
                restore_database_url=args.restore_database,
            )
            if args.manifest is not None:
                _write_new_text(args.manifest, json.dumps(plan, indent=2, sort_keys=True) + "\n")
                print("Redacted Postgres backup plan written.")
            else:
                print(json.dumps(plan, indent=2, sort_keys=True))
        return

    if args.command == "restore":
        from .backup import restore_sqlite_backup

        result = restore_sqlite_backup(
            args.backup,
            args.destination,
            manifest_path=args.manifest,
            verify=args.verify,
        )
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "migrate":
        from .config import load_settings
        from .migrate import format_migration_result, run_migration

        database = args.database or load_settings().database_url
        operation = (
            "plan"
            if args.plan
            else "verify"
            if args.verify
            else "dry_run"
            if args.dry_run
            else "migrate"
        )
        result = run_migration(
            database,
            operation=operation,
            target=args.target,
        )
        print(format_migration_result(result, json_output=args.json))
        return

    if args.command == "migrate-v2-data":
        from .application.migrate_v2 import run_v2_backfill
        from .config import load_settings

        database = args.database or load_settings().database_url
        result = run_v2_backfill(
            database,
            batch_size=args.batch_size,
            resume=args.resume,
        )
        if args.json:
            print(json.dumps(result.to_dict(), sort_keys=True))
        else:
            print(
                "v2 data backfill: "
                f"status={result.status} "
                f"database_kind={result.database_kind} "
                f"processed={result.processed_count} "
                f"warnings={result.warning_count} "
                f"errors={result.error_count}"
            )
        return

    parser.error(f"unknown command: {args.command}")


def _write_new_text(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
    if os.name != "nt":
        path.chmod(0o600)


if __name__ == "__main__":
    main()
