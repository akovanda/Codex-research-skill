from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path

from .codex_install import (
    diagnose_codex_install,
    format_codex_install_report,
    install_codex,
    managed_codex_install_present,
    uninstall_codex,
)
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
        description=(
            "Manage the local Research Registry runtime: personal SQLite "
            "and shared deployments."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser(
        "init",
        help="Initialize the private XDG SQLite database and blob storage.",
    )
    init.add_argument(
        "--json",
        action="store_true",
        help="Emit a content-free structured initialization result.",
    )
    init.add_argument(
        "--install-codex",
        action="store_true",
        help="Also install the bundled Codex plugin after local initialization.",
    )

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
    subparsers.add_parser(
        "doctor",
        help="Check the runtime and installed Codex integration without printing secrets.",
    )

    install_codex_parser = subparsers.add_parser(
        "install-codex",
        help="Install the focused local Research Registry plugin for Codex.",
    )
    install_codex_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List exact managed file and Codex registration changes without writing.",
    )

    uninstall_codex_parser = subparsers.add_parser(
        "uninstall-codex",
        help="Remove the managed Research Registry plugin and restore migrated managed state.",
    )
    uninstall_codex_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List exact managed file and Codex registration changes without writing.",
    )

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

    serve = subparsers.add_parser(
        "serve",
        help="Run the optional local web review server with explicit authentication.",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host. Defaults to loopback.",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port. Defaults to the local config port.",
    )
    subparsers.add_parser(
        "web",
        help="Compatibility alias for the directly configured web app.",
    )

    mcp = subparsers.add_parser(
        "mcp",
        help="Run the Research Registry MCP server.",
    )
    mcp.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="MCP transport. The local plugin uses stdio.",
    )
    mcp.add_argument(
        "--database",
        default=None,
        help=(
            "SQLite path/URL or Postgres URL. Defaults to the configured "
            "Research Registry database."
        ),
    )

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
    backup.add_argument(
        "--blob-root",
        type=Path,
        default=None,
        help="Filesystem blob root. Defaults to the configured data directory.",
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
    restore.add_argument(
        "--blob-root",
        type=Path,
        default=None,
        help="Filesystem blob root used to verify referenced objects.",
    )

    blob_health = subparsers.add_parser(
        "blob-health",
        help="Inspect database-to-blob integrity and orphan inventory.",
    )
    blob_health.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    blob_health.add_argument(
        "--blob-root",
        type=Path,
        default=None,
        help="Filesystem blob root. Defaults to the configured data directory.",
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

    rebuild_search = subparsers.add_parser(
        "rebuild-search",
        aliases=["rebuild-search-index"],
        help="Transactionally rebuild and verify the canonical search projection.",
    )
    rebuild_search.add_argument(
        "--database",
        default=None,
        help="SQLite path/URL or Postgres URL. Defaults to the configured database.",
    )
    rebuild_search.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the dialect full-text index integrity check.",
    )
    rebuild_search.add_argument(
        "--json",
        action="store_true",
        help="Emit the content-free rebuild result as JSON.",
    )

    retrieval_eval = subparsers.add_parser(
        "eval-retrieval",
        help="Run the deterministic retrieval corpus and parity evaluation.",
    )
    retrieval_eval.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Synthetic or private local retrieval corpus JSON.",
    )
    retrieval_eval.add_argument(
        "--postgres-database",
        default=None,
        help=(
            "Optional Postgres URL for parity. Defaults to TEST_DATABASE_URL "
            "when configured."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "init":
        from .local_personal import initialize_personal_registry

        result = initialize_personal_registry()
        payload = {
            "status": "initialized" if result.created else "current",
            "database_kind": "sqlite",
            "migration_state": result.migration_state,
            "applied_migrations": list(result.applied_migrations),
            "config_path": str(result.paths.config_path),
            "data_dir": str(result.paths.data_dir),
            "database_path": str(result.paths.database_path),
            "blob_root": str(result.paths.blob_root),
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for key, value in payload.items():
                rendered = (
                    ",".join(value)
                    if isinstance(value, list)
                    else str(value).lower()
                    if isinstance(value, bool)
                    else str(value)
                )
                print(f"{key}={rendered}")
        if args.install_codex:
            print(
                format_codex_install_report(
                    install_codex()
                )
            )
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
        from .managed_config import load_managed_local_config

        managed = load_managed_local_config()
        if managed is None or managed.deployment_mode == "personal":
            from .local_personal import diagnose_personal_registry

            checks = diagnose_personal_registry()
            if managed_codex_install_present():
                checks += diagnose_codex_install()
            print(format_doctor(checks))
        else:
            print(
                format_doctor(
                    diagnose_local_runtime() + diagnose_codex_install()
                )
            )
        return

    if args.command == "install-codex":
        if not args.dry_run:
            _initialize_personal_default()
        print(
            format_codex_install_report(
                install_codex(dry_run=args.dry_run)
            )
        )
        return

    if args.command == "uninstall-codex":
        print(
            format_codex_install_report(
                uninstall_codex(dry_run=args.dry_run)
            )
        )
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

    if args.command == "serve":
        token = os.environ.get("RESEARCH_REGISTRY_ADMIN_TOKEN")
        if not token:
            parser.error(
                "serve requires RESEARCH_REGISTRY_ADMIN_TOKEN"
            )
        _initialize_personal_default()
        os.environ["RESEARCH_REGISTRY_ADMIN_TOKEN"] = token
        os.environ["RESEARCH_REGISTRY_HOST"] = args.host
        if args.port is not None:
            os.environ["RESEARCH_REGISTRY_PORT"] = str(args.port)
        from .web import main as web_main

        web_main()
        return

    if args.command == "web":
        from .web import main as web_main

        web_main()
        return

    if args.command == "mcp":
        _run_mcp_stdio(args.database)
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
        from .managed_config import load_managed_local_config

        settings = load_settings()
        database = args.database or settings.database_url
        blob_root = args.blob_root or settings.data_dir / "blobs"
        target = resolve_database_target(database)
        if target.kind == "sqlite":
            manifest_path = args.manifest or args.output.with_suffix(args.output.suffix + ".manifest.json")
            managed = (
                load_managed_local_config()
                if args.database is None
                else None
            )
            config_source = (
                managed.config_path
                if managed is not None
                and managed.deployment_mode == "personal"
                else None
            )
            config_destination = (
                args.output.with_suffix(
                    args.output.suffix + ".config.toml"
                )
                if config_source is not None
                else None
            )
            manifest = backup_sqlite(
                target,
                args.output,
                manifest_path=manifest_path,
                blob_root=blob_root,
                config_path=config_source,
                config_destination=config_destination,
            )
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "database_kind": "sqlite",
                        "sha256": manifest["artifacts"][0]["sha256"],
                        "configuration": manifest["configuration"]["status"],
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
        from .config import load_settings

        settings = load_settings()

        result = restore_sqlite_backup(
            args.backup,
            args.destination,
            manifest_path=args.manifest,
            verify=args.verify,
            blob_root=args.blob_root or settings.data_dir / "blobs",
        )
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "blob-health":
        from .application.source_versions import SourceVersionService
        from .config import load_settings
        from .ingestion.blobs import FilesystemBlobStore

        settings = load_settings()
        database = args.database or settings.database_url
        blob_root = args.blob_root or settings.data_dir / "blobs"
        report = SourceVersionService(
            database,
            FilesystemBlobStore(blob_root),
        ).inspect_blob_health()
        print(json.dumps(report.to_dict(), sort_keys=True))
        if not report.healthy:
            raise SystemExit(1)
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

    if args.command in {"rebuild-search", "rebuild-search-index"}:
        from .config import load_settings
        from .retrieval.projection import SearchIndexService

        database = args.database or load_settings().database_url
        result = SearchIndexService(database).rebuild(
            verify=not args.no_verify
        )
        if args.json:
            print(json.dumps(result.to_dict(), sort_keys=True))
        else:
            print(
                "search index rebuild: "
                f"database_kind={result.database_kind} "
                f"documents={result.document_count} "
                f"verified={str(result.verified).lower()} "
                f"projection_sha256={result.projection_sha256}"
            )
        return

    if args.command == "eval-retrieval":
        from .retrieval.evaluation import run_retrieval_evaluation

        postgres_url = (
            args.postgres_database or os.environ.get("TEST_DATABASE_URL")
        )
        result = run_retrieval_evaluation(
            args.corpus,
            postgres_url=postgres_url,
        )
        print(json.dumps(result.to_dict(), sort_keys=True))
        failed = (
            result.recall_at_5 < 0.70
            or result.exact_recall_at_1 < 1.0
            or (
                result.sqlite_postgres_overlap is not None
                and result.sqlite_postgres_overlap < 0.90
            )
        )
        if failed:
            raise SystemExit(1)
        return

    parser.error(f"unknown command: {args.command}")


def _write_new_text(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
    if os.name != "nt":
        path.chmod(0o600)


def _run_mcp_stdio(database: str | None) -> None:
    if database:
        if "://" in database:
            database_url = database
        else:
            database_path = Path(database).expanduser().resolve()
            database_url = f"sqlite:///{database_path}"
        os.environ["RESEARCH_REGISTRY_DATABASE_URL"] = database_url

    from .mcp_server import main as mcp_main

    mcp_main()


def _initialize_personal_default() -> None:
    from .local_personal import initialize_personal_registry_if_unconfigured

    initialize_personal_registry_if_unconfigured()


if __name__ == "__main__":
    main()
