from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version

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

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
