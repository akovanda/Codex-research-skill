from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class GateResult:
    passed: bool
    missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "missing": list(self.missing)}


@dataclass(frozen=True)
class ReleaseAssessment:
    level: str
    gates: dict[str, GateResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "research-registry-release-assessment/v1",
            "level": self.level,
            "gates": {
                name: result.to_dict()
                for name, result in self.gates.items()
            },
        }


def assess_release(
    *,
    retrieval: Mapping[str, Any],
    automated: Mapping[str, bool],
    operator: Mapping[str, bool],
) -> ReleaseAssessment:
    """Apply the packet's fixed alpha, beta, and stable thresholds."""
    alpha_requirements = {
        "automated.v1_tests": _flag(automated, "v1_tests"),
        "automated.migration_fixtures": _flag(
            automated, "migration_fixtures"
        ),
        "automated.atomic_deposit": _flag(automated, "atomic_deposit"),
        "automated.local_stdio": _flag(automated, "local_stdio"),
        "automated.security_suite": _flag(automated, "security_suite"),
        "automated.backup_restore": _flag(automated, "backup_restore"),
        "automated.package_artifacts": _flag(
            automated, "package_artifacts"
        ),
    }
    alpha = _gate(alpha_requirements)

    beta_requirements = {
        "gate.alpha": alpha.passed,
        "retrieval.recall_at_5>=0.75": _metric(
            retrieval, "recall_at_5", 0.75
        ),
        "retrieval.evidence_resolvability>=0.90": _metric(
            retrieval, "evidence_resolvability", 0.90
        ),
        "retrieval.sqlite_postgres_overlap>=0.90": _metric(
            retrieval, "sqlite_postgres_overlap", 0.90
        ),
        "automated.plugin": _flag(automated, "plugin"),
        "automated.review_refresh": _flag(automated, "review_refresh"),
        "automated.ingestion_security": _flag(
            automated, "ingestion_security"
        ),
        "automated.legacy_hidden": _flag(automated, "legacy_hidden"),
        "operator.real_v1_migration": _flag(
            operator, "real_v1_migration"
        ),
        "operator.shared_compose": _flag(operator, "shared_compose"),
    }
    beta = _gate(beta_requirements)

    stable_requirements = {
        "gate.beta": beta.passed,
        "retrieval.recall_at_5>=0.80": _metric(
            retrieval, "recall_at_5", 0.80
        ),
        "retrieval.evidence_resolvability>=0.95": _metric(
            retrieval, "evidence_resolvability", 0.95
        ),
        "retrieval.exact_recall_at_1=1.0": _metric(
            retrieval, "exact_recall_at_1", 1.0
        ),
        "automated.zero_migration_data_loss": _flag(
            automated, "zero_migration_data_loss"
        ),
        "automated.zero_partial_deposits": _flag(
            automated, "zero_partial_deposits"
        ),
        "automated.docs": _flag(automated, "docs"),
        "automated.schemas_frozen": _flag(automated, "schemas_frozen"),
        "automated.sbom_provenance": _flag(
            automated, "sbom_provenance"
        ),
        "automated.upgrade_rollback": _flag(
            automated, "upgrade_rollback"
        ),
        "operator.security_review": _flag(operator, "security_review"),
    }
    stable = _gate(stable_requirements)
    level = (
        "stable"
        if stable.passed
        else "beta"
        if beta.passed
        else "alpha"
        if alpha.passed
        else "blocked"
    )
    return ReleaseAssessment(
        level=level,
        gates={"alpha": alpha, "beta": beta, "stable": stable},
    )


def _gate(requirements: Mapping[str, bool]) -> GateResult:
    missing = tuple(
        name for name, passed in requirements.items() if not passed
    )
    return GateResult(passed=not missing, missing=missing)


def _flag(values: Mapping[str, bool], name: str) -> bool:
    return values.get(name) is True


def _metric(
    values: Mapping[str, Any],
    name: str,
    minimum: float,
) -> bool:
    value = values.get(name)
    return isinstance(value, int | float) and value >= minimum
