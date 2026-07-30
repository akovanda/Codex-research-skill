from __future__ import annotations

import json
from pathlib import Path
import re

from research_registry.release.artifacts import (
    build_provenance_statement,
    build_spdx_sbom,
    stage_release_artifacts,
    write_sha256_manifest,
)
from research_registry.release.gates import assess_release
from research_registry.release.rehearsal import rehearse_sqlite_upgrade
from research_registry.service import RegistryService


ROOT = Path(__file__).parents[1]


def test_release_assessment_is_honest_about_missing_operator_gates() -> None:
    assessment = assess_release(
        retrieval={
            "recall_at_5": 1.0,
            "evidence_resolvability": 1.0,
            "exact_recall_at_1": 1.0,
            "sqlite_postgres_overlap": None,
        },
        automated={
            "v1_tests": True,
            "migration_fixtures": True,
            "atomic_deposit": True,
            "local_stdio": True,
            "security_suite": True,
            "backup_restore": True,
            "package_artifacts": True,
            "plugin": True,
            "review_refresh": True,
            "ingestion_security": True,
            "legacy_hidden": True,
            "docs": True,
            "schemas_frozen": True,
            "sbom_provenance": True,
            "upgrade_rollback": True,
        },
        operator={
            "real_v1_migration": False,
            "shared_compose": False,
            "security_review": False,
        },
    )

    assert assessment.level == "alpha"
    assert assessment.gates["alpha"].passed is True
    assert assessment.gates["beta"].passed is False
    assert assessment.gates["stable"].passed is False
    assert "operator.real_v1_migration" in assessment.gates["beta"].missing
    assert "operator.security_review" in assessment.gates["stable"].missing


def test_sbom_and_provenance_are_deterministic_and_hash_artifacts(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "research_registry-0.1.0-py3-none-any.whl"
    artifact.write_bytes(b"deterministic release artifact")

    sbom = build_spdx_sbom(
        project_name="research-registry",
        project_version="0.1.0",
        dependencies={"pydantic": "2.11.0", "fastapi": "0.116.0"},
    )
    provenance = build_provenance_statement(
        [artifact],
        source_uri="https://github.com/akovanda/Codex-research-skill",
        source_revision="f" * 40,
    )

    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert [package["name"] for package in sbom["packages"]] == [
        "fastapi",
        "pydantic",
        "research-registry",
    ]
    assert provenance["_type"] == "https://in-toto.io/Statement/v1"
    assert provenance["subject"][0]["name"] == artifact.name
    assert len(provenance["subject"][0]["digest"]["sha256"]) == 64
    assert json.dumps(sbom, sort_keys=True) == json.dumps(
        build_spdx_sbom(
            project_name="research-registry",
            project_version="0.1.0",
            dependencies={"fastapi": "0.116.0", "pydantic": "2.11.0"},
        ),
        sort_keys=True,
    )


def test_release_bundle_is_self_contained_and_checksum_verifiable(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "research_registry-0.1.0-py3-none-any.whl"
    sdist = dist / "research_registry-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    bundle = tmp_path / "release"

    staged = stage_release_artifacts(
        [wheel, sdist],
        output_directory=bundle,
    )
    manifest = bundle / "SHA256SUMS"
    write_sha256_manifest(staged, output_path=manifest)

    assert [path.parent for path in staged] == [bundle, bundle]
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", maxsplit=1)
        artifact = bundle / name
        assert artifact.is_file()
        assert len(digest) == 64


def test_sqlite_upgrade_backup_restore_and_rollback_rehearsal(
    tmp_path: Path,
) -> None:
    result = rehearse_sqlite_upgrade(tmp_path)

    assert result.fresh_install is True
    assert result.upgrade is True
    assert result.backup is True
    assert result.restore is True
    assert result.rollback is True
    assert result.data_loss_count == 0
    assert result.unresolved_migration_errors == 0

    restored = RegistryService(result.restored_database)
    with restored.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM questions"
        ).fetchone()["count"] == 1


def test_all_workflow_actions_are_pinned_to_immutable_commits() -> None:
    action = re.compile(r"^\s*-\s+uses:\s+([^#\s]+)", re.MULTILINE)
    pinned = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
    references: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        references.extend(action.findall(workflow.read_text(encoding="utf-8")))

    assert references
    assert all(pinned.fullmatch(reference) for reference in references)
