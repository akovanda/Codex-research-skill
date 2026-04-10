from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "research-memory-retrieval"


def test_skill_package_has_required_files() -> None:
    assert (SKILL_DIR / "SKILL.md").exists()
    assert (SKILL_DIR / "agents" / "openai.yaml").exists()
    assert (SKILL_DIR / "references" / "topic-taxonomy.md").exists()
    assert (SKILL_DIR / "references" / "workflow.md").exists()
    assert (SKILL_DIR / "references" / "deposit-rubric.md").exists()


def test_skill_instructions_cover_search_first_and_guardrails() -> None:
    content = (SKILL_DIR / "SKILL.md").read_text()

    assert "Search existing registry content first" in content
    assert "Refuse to create unsupported artifacts" in content
    assert "Publish only when explicitly asked" in content
    assert "requires a configured Research Registry MCP server" in content


def test_skill_metadata_has_no_todo_placeholders() -> None:
    skill_md = (SKILL_DIR / "SKILL.md").read_text()
    openai_yaml = (SKILL_DIR / "agents" / "openai.yaml").read_text()

    assert "[TODO:" not in skill_md
    assert "research-memory-retrieval" in skill_md
    assert 'allow_implicit_invocation: true' in openai_yaml
