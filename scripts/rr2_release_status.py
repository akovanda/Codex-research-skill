from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from research_registry.release.gates import assess_release
from research_registry.retrieval.evaluation import run_retrieval_evaluation


ROOT = Path(__file__).resolve().parents[1]
AUTOMATED_GATES = {
    "v1_tests",
    "migration_fixtures",
    "atomic_deposit",
    "local_stdio",
    "security_suite",
    "backup_restore",
    "package_artifacts",
    "plugin",
    "review_refresh",
    "ingestion_security",
    "legacy_hidden",
    "zero_migration_data_loss",
    "zero_partial_deposits",
    "docs",
    "schemas_frozen",
    "sbom_provenance",
    "upgrade_rollback",
}
RELEASE_ORDER = {"blocked": 0, "alpha": 1, "beta": 2, "stable": 3}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assess fixed RR2 alpha/beta/stable release gates."
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "evals" / "retrieval" / "synthetic.json",
    )
    parser.add_argument(
        "--automated",
        action="store_true",
        help="Record that the composed automated gate commands just passed.",
    )
    parser.add_argument(
        "--operator-evidence",
        type=Path,
        default=None,
        help="Optional local JSON booleans for operator-only gates.",
    )
    parser.add_argument(
        "--require",
        choices=("alpha", "beta", "stable"),
        default=None,
    )
    args = parser.parse_args()

    retrieval = run_retrieval_evaluation(
        args.corpus,
        postgres_url=os.environ.get("TEST_DATABASE_URL"),
    ).to_dict()
    automated = {
        name: bool(args.automated) for name in AUTOMATED_GATES
    }
    operator = _operator_evidence(args.operator_evidence)
    assessment = assess_release(
        retrieval=retrieval,
        automated=automated,
        operator=operator,
    )
    output = {
        **assessment.to_dict(),
        "retrieval": {
            field: retrieval[field]
            for field in (
                "recall_at_5",
                "evidence_resolvability",
                "exact_recall_at_1",
                "sqlite_postgres_overlap",
                "postgres_status",
            )
        },
        "operator_evidence": operator,
        "limitations": [
            "operator-local private known-answer corpus is not checked in",
            "release provenance is unsigned until the maintainer signs it",
            "no artifact, image, tag, or package was published",
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    if args.require is not None and (
        RELEASE_ORDER[assessment.level] < RELEASE_ORDER[args.require]
    ):
        raise SystemExit(1)


def _operator_evidence(path: Path | None) -> dict[str, bool]:
    result = {
        "real_v1_migration": False,
        "shared_compose": False,
        "security_review": False,
    }
    if path is None:
        return result
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != set(result):
        raise ValueError(
            "operator evidence must contain exactly real_v1_migration, "
            "shared_compose, and security_review"
        )
    if any(not isinstance(value, bool) for value in payload.values()):
        raise ValueError("operator evidence values must be booleans")
    return payload


if __name__ == "__main__":
    main()
