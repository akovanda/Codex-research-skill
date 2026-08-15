from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "SUPPORT.md",
    REPO_ROOT / "docs" / "getting-started.md",
    REPO_ROOT / "docs" / "api-quickstart.md",
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "deploy-local.md",
    REPO_ROOT / "docs" / "deploy-shared-compose.md",
    REPO_ROOT / "docs" / "deploy-kubernetes.md",
    REPO_ROOT / "docs" / "operations.md",
    REPO_ROOT / "docs" / "codex-plugin.md",
    REPO_ROOT / "docs" / "migrate-managed-postgres.md",
    REPO_ROOT / "docs" / "implicit-research-capture.md",
    REPO_ROOT / "docs" / "repo-aware-capture.md",
    REPO_ROOT / "docs" / "memory-retrieval-skill.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "RELEASE.md",
]
TEXT_SCAN_ROOTS = [
    REPO_ROOT / "src",
    REPO_ROOT / "tests",
    REPO_ROOT / "docs",
    REPO_ROOT / "README.md",
    REPO_ROOT / "Makefile",
    REPO_ROOT / ".codex",
]


def test_primary_docs_do_not_include_repo_local_absolute_paths() -> None:
    forbidden_home = "/" + "home/"
    for path in PRIMARY_DOCS:
        content = path.read_text(encoding="utf-8")
        assert forbidden_home not in content


def test_repo_text_files_do_not_include_user_specific_absolute_paths() -> None:
    forbidden_patterns = (
        "/" + "home/",
        "/" + "Users/",
        "C:" + "\\Users\\",
    )
    allowed_suffixes = {
        ".md",
        ".py",
        ".toml",
        ".json",
        ".yaml",
        ".yml",
        ".txt",
        ".sql",
        ".html",
        ".css",
        ".sh",
    }
    allowed_names = {
        "Makefile",
        "Dockerfile",
        ".env.example",
        ".dockerignore",
        "LICENSE",
    }

    files: list[Path] = []
    for root in TEXT_SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(path for path in root.rglob("*") if path.is_file())

    offenders: list[str] = []
    for path in files:
        if path.suffix not in allowed_suffixes and path.name not in allowed_names:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern in content for pattern in forbidden_patterns):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_primary_docs_present_question_led_model() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for term in ("Question", "ResearchSession", "Excerpt", "Claim", "Report"):
        assert term in readme
    for legacy_term in ("`Annotation`", "`Finding`", "`Run`"):
        assert legacy_term not in readme


def test_open_source_preview_surface_files_exist() -> None:
    required = [
        REPO_ROOT / "Makefile",
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "SECURITY.md",
        REPO_ROOT / "SUPPORT.md",
        REPO_ROOT / "Dockerfile",
        REPO_ROOT / ".dockerignore",
        REPO_ROOT / ".env.example",
        REPO_ROOT / ".codex" / "repo-profile.toml",
        REPO_ROOT / "deploy" / "compose.yaml",
        REPO_ROOT / "deploy" / ".env.example",
        REPO_ROOT / "deploy" / "kubernetes" / "deployment.yaml",
        REPO_ROOT / "deploy" / "kubernetes" / "service.yaml",
        REPO_ROOT / "deploy" / "kubernetes" / "migrate-job.yaml",
        REPO_ROOT / "docs" / "getting-started.md",
        REPO_ROOT / "docs" / "api-quickstart.md",
        REPO_ROOT / "docs" / "migrate-managed-postgres.md",
        REPO_ROOT / "docs" / "repo-aware-capture.md",
    ]
    for path in required:
        assert path.exists(), str(path)


def test_release_scope_docs_are_consistent() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    deploy_local = (REPO_ROOT / "docs" / "deploy-local.md").read_text(encoding="utf-8")
    deploy_shared = (REPO_ROOT / "docs" / "deploy-shared-compose.md").read_text(encoding="utf-8")
    deploy_kubernetes = (REPO_ROOT / "docs" / "deploy-kubernetes.md").read_text(encoding="utf-8")
    getting_started = (REPO_ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    operations = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = (REPO_ROOT / "RELEASE.md").read_text(encoding="utf-8")
    support = (REPO_ROOT / "SUPPORT.md").read_text(encoding="utf-8")
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    api_quickstart = (REPO_ROOT / "docs" / "api-quickstart.md").read_text(encoding="utf-8")

    assert "GitHub-first open-source preview" in readme
    assert "make init" in readme
    assert "research-registry backup" in readme
    assert "What success looks like" in readme
    assert "personal SQLite plus filesystem blobs" in readme
    assert "shared self-hosted Compose deployment for internal teams" in readme
    assert "direct public-internet exposure" in readme
    assert "Windows: not yet claimed" in readme
    assert "/docs" in readme
    assert "OpenAPI JSON" in readme
    assert "make init" in deploy_local
    assert "research-registry backup" in deploy_local
    assert "make shared-up" in deploy_local
    assert "make init" in getting_started
    assert "research-registry doctor" in getting_started
    assert "research-registry backup" in getting_started
    assert "make shared-up" in getting_started
    assert "tokenless same-user STDIO MCP" in architecture
    assert "internal-only" in deploy_shared
    assert "example-only" in deploy_kubernetes
    assert "no daemon, system service, network listener" in deploy_local
    assert "`v0.1.0` preview" in operations
    assert "Initial open-source preview release." in changelog
    assert "GitHub source releases" in release
    assert "make preview-check" in release
    assert "make workflow-check" in release
    assert "make grounded-pass-check" in release
    assert "real maintainer-owned security contact" in release
    assert "Linux" in support
    assert "macOS" in support
    assert "Windows localhost installs" in support
    assert "GitHub issues" in support
    assert "make preview-check" in makefile
    assert "make workflow-check" in makefile
    assert "make grounded-pass-check" in makefile
    assert "make init" in makefile
    assert "make shared-up" in makefile
    assert "make backup" in makefile
    assert "/openapi.json" in api_quickstart
    assert "/api/admin/api-keys" in api_quickstart
    assert "/api/import/bibtex" in api_quickstart
    assert "/api/briefs/resolve" in api_quickstart
    assert "/api/reports/$REPORT_ID/refresh" in api_quickstart
    assert "/api/follow-ups/$FOLLOW_UP_ID/status" in api_quickstart
    assert "make workflow-check" in readme
    assert "make grounded-pass-check" in readme
    assert "POST /api/import/bibtex" in readme
    assert "POST /api/briefs/resolve" in readme
    assert ".codex/repo-profile.toml" in readme


def test_package_metadata_matches_preview_contract() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["name"] == "research-registry"
    assert project["version"] == "0.2.0a2"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert any(author["name"] == "Research Registry contributors" for author in project["authors"])
    assert "research" in project["keywords"]
    assert "mcp" in project["keywords"]
    assert "Programming Language :: Python :: 3.12" in project["classifiers"]

    urls = project.get("urls", {})
    assert not any("github.com/example/research-registry" in value for value in urls.values())
    assert project["scripts"]["research-registry"] == "research_registry.cli:main"
    assert project["scripts"]["research-registry-web"] == "research_registry.web:main"


def test_packaged_distribution_declares_skill_assets() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    packaged_files = {file for files in data_files.values() for file in files}

    for path in [
        "skills/research-capture/SKILL.md",
        "skills/research-capture/agents/openai.yaml",
        "skills/research-capture/references/workflow.md",
        "skills/research-memory-retrieval/SKILL.md",
        "skills/research-memory-retrieval/agents/openai.yaml",
        "skills/research-memory-retrieval/references/topic-taxonomy.md",
    ]:
        assert path in packaged_files


def test_codex_plugin_docs_state_safe_v2_policy_and_legacy_boundary() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "codex-plugin.md").read_text(
        encoding="utf-8"
    )
    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")

    for term in (
        "research-registry install-codex --dry-run",
        "research-registry uninstall-codex --dry-run",
        "CODEX_HOME",
        "local STDIO",
        "allow_implicit_invocation: false",
        "never publishes",
        "untrusted data",
    ):
        assert term in guide
    assert "docs/codex-plugin.md" in readme
    assert "remote HTTP" in guide
    assert "raw Codex subprocess output" in security
