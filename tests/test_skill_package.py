from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_SKILL_DIR = REPO_ROOT / "skills" / "research-memory-retrieval"
CAPTURE_SKILL_DIR = REPO_ROOT / "skills" / "research-capture"


def test_memory_skill_package_has_required_files() -> None:
    assert (MEMORY_SKILL_DIR / "SKILL.md").exists()
    assert (MEMORY_SKILL_DIR / "agents" / "openai.yaml").exists()
    assert (MEMORY_SKILL_DIR / "references" / "topic-taxonomy.md").exists()
    assert (MEMORY_SKILL_DIR / "references" / "workflow.md").exists()
    assert (MEMORY_SKILL_DIR / "references" / "deposit-rubric.md").exists()


def test_memory_skill_instructions_cover_search_first_and_guardrails() -> None:
    content = (MEMORY_SKILL_DIR / "SKILL.md").read_text()

    assert "Search existing registry content first" in content
    assert "Refuse to create unsupported artifacts" in content
    assert "Publish only when explicitly asked" in content
    assert "requires a configured Research Registry MCP server" in content
    assert "delegated to by `$research-capture`" in content or "invoked directly or delegated to by `$research-capture`" in content


def test_memory_skill_metadata_has_no_todo_placeholders() -> None:
    skill_md = (MEMORY_SKILL_DIR / "SKILL.md").read_text()
    openai_yaml = (MEMORY_SKILL_DIR / "agents" / "openai.yaml").read_text()

    assert "[TODO:" not in skill_md
    assert "research-memory-retrieval" in skill_md
    assert 'allow_implicit_invocation: true' in openai_yaml


def test_capture_skill_package_has_required_files() -> None:
    assert (CAPTURE_SKILL_DIR / "SKILL.md").exists()
    assert (CAPTURE_SKILL_DIR / "agents" / "openai.yaml").exists()
    assert (CAPTURE_SKILL_DIR / "references" / "workflow.md").exists()
    assert (CAPTURE_SKILL_DIR / "references" / "routing.md").exists()
    assert (CAPTURE_SKILL_DIR / "references" / "queue-fallback.md").exists()
    assert (CAPTURE_SKILL_DIR / "references" / "repo-aware.md").exists()


def test_capture_skill_instructions_cover_implicit_capture_and_queue() -> None:
    content = (CAPTURE_SKILL_DIR / "SKILL.md").read_text()
    openai_yaml = (CAPTURE_SKILL_DIR / "agents" / "openai.yaml").read_text()
    repo_aware = (CAPTURE_SKILL_DIR / "references" / "repo-aware.md").read_text()

    assert "trigger on research intent" in content.lower() or "research intent" in content.lower()
    assert "Flush pending queue items first" in content
    assert "`$research-memory-retrieval`" in content
    assert "Always create a guidance report" in content
    assert ".codex/repo-profile.toml" in content
    assert "AGENTS.md" in content
    assert "research-registry-capture-queue enqueue" in (CAPTURE_SKILL_DIR / "references" / "queue-fallback.md").read_text()
    assert "exact command for a file" in repo_aware
    assert 'allow_implicit_invocation: true' in openai_yaml
    assert "[TODO:" not in content
