from __future__ import annotations

import argparse

from .local_manager import ensure_prerequisites, format_status, install_local_runtime, local_runtime_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the shared localhost Research Registry runtime for local Codex instances.")
    parser.add_argument("--port", type=int, default=None, help="Host port for the local registry. Defaults to the existing managed port or 8010.")
    parser.add_argument("--skip-build", action="store_true", help="Skip rebuilding the Docker image.")
    parser.add_argument("--skip-start", action="store_true", help="Write config and Compose files but do not start the stack.")
    parser.add_argument("--skip-codex-config", action="store_true", help="Do not patch ~/.codex/config.toml.")
    parser.add_argument("--skip-skill-install", action="store_true", help="Do not symlink the local skills into ~/.codex/skills.")
    args = parser.parse_args()

    ensure_prerequisites()
    install_local_runtime(
        port=args.port,
        build_image=not args.skip_build,
        start_stack=not args.skip_start,
        configure_codex=not args.skip_codex_config,
        install_skills=not args.skip_skill_install,
    )
    print(format_status(local_runtime_status()))


if __name__ == "__main__":
    main()
