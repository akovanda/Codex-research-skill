from __future__ import annotations

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
