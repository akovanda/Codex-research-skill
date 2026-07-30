from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from research_registry.release.rehearsal import rehearse_sqlite_upgrade


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Soak copied SQLite upgrade and rollback rehearsals."
    )
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.iterations < 1 or args.iterations > 100:
        raise SystemExit("iterations must be between 1 and 100")
    with tempfile.TemporaryDirectory(
        prefix="research-registry-release-rehearsal-"
    ) as temporary:
        results = [
            rehearse_sqlite_upgrade(Path(temporary) / f"iteration-{index}")
            for index in range(1, args.iterations + 1)
        ]
        passed = [
            all(
                (
                    result.fresh_install,
                    result.upgrade,
                    result.backup,
                    result.restore,
                    result.rollback,
                    result.data_loss_count == 0,
                    result.unresolved_migration_errors == 0,
                )
            )
            for result in results
        ]
        print(
            json.dumps(
                {
                    "protocol": "research-registry-migration-soak/v1",
                    "iterations": args.iterations,
                    "passed_iterations": sum(passed),
                    "data_loss_count": sum(
                        result.data_loss_count for result in results
                    ),
                    "unresolved_migration_errors": sum(
                        result.unresolved_migration_errors
                        for result in results
                    ),
                    "total_duration_ms": round(
                        sum(result.duration_ms for result in results),
                        3,
                    ),
                    "temporary_artifacts_removed": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        if not all(passed):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
