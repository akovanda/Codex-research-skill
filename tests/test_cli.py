from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_module_help_shows_cli_without_starting_server() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "research_registry", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Manage the local Research Registry runtime" in result.stdout
    assert "up" in result.stdout
    assert "doctor" in result.stdout
    assert "Application startup complete" not in result.stdout


def test_cli_up_help_exposes_package_install_options() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "research_registry", "up", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--image" in result.stdout
    assert "--build-local-image" in result.stdout
    assert "--skip-pull" in result.stdout


def test_cli_audit_backup_and_restore_help() -> None:
    for command, expected in (
        ("audit-data", "--markdown-out"),
        ("backup", "--output"),
        ("restore", "--verify"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "research_registry", command, "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert expected in result.stdout


def test_cli_migrate_help_exposes_safe_operator_modes() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "research_registry", "migrate", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    for expected in ("--plan", "--verify", "--dry-run", "--target", "--json"):
        assert expected in result.stdout


def test_cli_migrate_plan_json_does_not_create_schema_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cli-plan.sqlite3"
    env = os.environ.copy()
    env["RESEARCH_REGISTRY_DATABASE_URL"] = f"sqlite:///{database}"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_registry",
            "migrate",
            "--plan",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["operation"] == "plan"
    assert payload["database_kind"] == "sqlite"
    assert payload["pending_ids"] == [
        "0001_initial",
        "0002_workflows_and_trust",
    ]
    assert not database.exists()
