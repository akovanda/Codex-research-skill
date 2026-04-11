from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "deploy-local.md",
    REPO_ROOT / "docs" / "deploy-shared-compose.md",
    REPO_ROOT / "docs" / "deploy-kubernetes.md",
    REPO_ROOT / "docs" / "operations.md",
    REPO_ROOT / "docs" / "implicit-research-capture.md",
    REPO_ROOT / "docs" / "memory-retrieval-skill.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "RELEASE.md",
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


def test_release_scope_docs_are_consistent() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    deploy_local = (REPO_ROOT / "docs" / "deploy-local.md").read_text(encoding="utf-8")
    deploy_shared = (REPO_ROOT / "docs" / "deploy-shared-compose.md").read_text(encoding="utf-8")
    deploy_kubernetes = (REPO_ROOT / "docs" / "deploy-kubernetes.md").read_text(encoding="utf-8")
    operations = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = (REPO_ROOT / "RELEASE.md").read_text(encoding="utf-8")

    assert "GitHub-first open-source preview" in readme
    assert "managed localhost runtime for multiple local Codex instances" in readme
    assert "shared self-hosted Compose deployment for internal teams" in readme
    assert "direct public-internet exposure" in readme
    assert "managed localhost runtime on `127.0.0.1:8010`" in architecture
    assert "internal-only" in deploy_shared
    assert "example-only" in deploy_kubernetes
    assert "development-only" in deploy_local
    assert "`v0.1.0` preview" in operations
    assert "Initial open-source preview release." in changelog
    assert "GitHub source releases" in release


def test_package_metadata_matches_preview_contract() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["name"] == "research-registry"
    assert project["version"] == "0.1.0"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert any(author["name"] == "Research Registry contributors" for author in project["authors"])
    assert "research" in project["keywords"]
    assert "mcp" in project["keywords"]
    assert "Programming Language :: Python :: 3.12" in project["classifiers"]

    urls = project.get("urls", {})
    assert not any("github.com/example/research-registry" in value for value in urls.values())
