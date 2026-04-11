from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "deploy-local.md",
    REPO_ROOT / "docs" / "deploy-shared-compose.md",
    REPO_ROOT / "docs" / "deploy-kubernetes.md",
    REPO_ROOT / "docs" / "implicit-research-capture.md",
    REPO_ROOT / "docs" / "memory-retrieval-skill.md",
]


def test_primary_docs_do_not_include_repo_local_absolute_paths() -> None:
    for path in PRIMARY_DOCS:
        content = path.read_text(encoding="utf-8")
        assert "/home/akovanda" not in content


def test_primary_docs_present_question_led_model() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for term in ("Question", "ResearchSession", "Excerpt", "Claim", "Report"):
        assert term in readme
    for legacy_term in ("`Annotation`", "`Finding`", "`Run`"):
        assert legacy_term not in readme


def test_open_source_preview_surface_files_exist() -> None:
    required = [
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "SECURITY.md",
        REPO_ROOT / "Dockerfile",
        REPO_ROOT / ".dockerignore",
        REPO_ROOT / ".env.example",
        REPO_ROOT / "deploy" / "compose.yaml",
        REPO_ROOT / "deploy" / ".env.example",
        REPO_ROOT / "deploy" / "kubernetes" / "deployment.yaml",
        REPO_ROOT / "deploy" / "kubernetes" / "service.yaml",
        REPO_ROOT / "deploy" / "kubernetes" / "migrate-job.yaml",
    ]
    for path in required:
        assert path.exists(), str(path)
