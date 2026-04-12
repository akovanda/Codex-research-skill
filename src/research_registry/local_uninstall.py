from __future__ import annotations

import argparse

from .local_manager import format_status, format_uninstall_result, local_runtime_status, uninstall_local_runtime


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stop the managed localhost runtime and remove the managed Codex integration."
    )
    parser.add_argument(
        "--restore-codex-backup",
        action="store_true",
        help="Restore ~/.codex/config.toml.research-registry.bak when present instead of just removing the managed MCP block.",
    )
    parser.add_argument(
        "--purge-data",
        action="store_true",
        help="Also remove the managed local config/data directories and docker volumes for the localhost stack.",
    )
    args = parser.parse_args()

    result = uninstall_local_runtime(
        restore_codex_backup=args.restore_codex_backup,
        purge_data=args.purge_data,
    )
    print(format_uninstall_result(result))
    print()
    print(format_status(local_runtime_status()))


if __name__ == "__main__":
    main()
