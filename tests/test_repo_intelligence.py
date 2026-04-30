from __future__ import annotations

from pathlib import Path

from research_registry.repo_intelligence import resolve_repo_capture_request, run_repo_capture
from research_registry.research_capture import is_research_request, run_implicit_research_capture
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "repo-intelligence.sqlite3")
    service.initialize()
    return service


def make_monolith_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "monolith-fixture"
    (repo / ".codex").mkdir(parents=True)
    (repo / "app" / "models").mkdir(parents=True)
    (repo / "spec" / "models").mkdir(parents=True)
    (repo / "script").mkdir()
    (repo / "private").mkdir()
    (repo / "workspaces" / "chat" / "src").mkdir(parents=True)
    (repo / "workspaces" / "chat" / "test").mkdir(parents=True)

    (repo / ".codex" / "repo-profile.toml").write_text(
        """
[repo]
name = "monolith-fixture"
default_test_command = "make test"
default_lint_command = "make test"
default_build_command = "make preview-check"

[policy]
prefer_non_interactive = true
forbid_destructive_git = true
no_full_repo_tests = true
long_timeout_commands = ["script/spring --non-interactive rspec"]
review_conventions = ["Keep reviewer notes scoped to the owning area."]

[setup]
required_tools = ["git"]
required_paths = ["Gemfile"]
coverage_paths = ["coverage/.resultset.json"]
startup_notes = ["Spring and the test database can take a while to boot."]

[[areas]]
name = "ruby-app"
globs = ["app/**/*.rb", "spec/**/*.rb"]
test_command = "script/spring --non-interactive rspec {test_targets}"
lint_command = "bundle exec rubocop {lint_targets}"
required_paths = ["script/spring"]
owners = ["app-platform"]
review_conventions = ["Call out missing specs for Ruby changes."]
coverage_paths = ["coverage/.resultset.json"]

[[areas]]
name = "private-js"
globs = ["private/**/*.js", "private/**/*.ts", "private/**/*.tsx"]
test_command = "yarn test {target}"
lint_command = "yarn eslint {lint_targets}"
required_paths = ["private/node_modules"]
owners = ["private-ui"]

[[areas]]
name = "workspace-ui"
globs = ["workspaces/*/src/**/*.*", "workspaces/*/test/**/*.*"]
test_command = "yarn workspace {workspace} test {test_targets} --watch=false"
lint_command = "yarn workspace {workspace} lint {lint_targets}"
build_command = "yarn workspace {workspace} build"
owners = ["workspace-ui"]
review_conventions = ["Mention the owning workspace in reviewer notes."]
coverage_paths = ["coverage/workspace-ui.lcov"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (repo / "AGENTS.md").write_text(
        "# Root guide\n"
        "- Do not run the full Ruby suite.\n"
        "- Prefer non-interactive commands.\n"
        "- Reviewers expect targeted test coverage.\n",
        encoding="utf-8",
    )
    (repo / "spec" / "AGENTS.md").write_text(
        "# Spec guide\n"
        "- Use script/spring --non-interactive rspec for Ruby specs.\n"
        "- Keep spec runs targeted to the file under review.\n",
        encoding="utf-8",
    )
    (repo / "workspaces" / "chat" / "AGENTS.md").write_text(
        "# Chat workspace\n"
        "- Use yarn workspace chat test <file> --watch=false.\n"
        "- Mention the owning workspace in reviewer notes.\n",
        encoding="utf-8",
    )
    (repo / "Gemfile").write_text("source 'https://example.test'\n", encoding="utf-8")
    (repo / ".rubocop.yml").write_text("AllCops:\n  NewCops: enable\n", encoding="utf-8")
    (repo / "script" / "spring").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (repo / "private" / "package.json").write_text('{"name":"private"}\n', encoding="utf-8")
    (repo / "workspaces" / "chat" / "package.json").write_text('{"name":"chat"}\n', encoding="utf-8")
    (repo / "app" / "models" / "ship.rb").write_text("class Ship\nend\n", encoding="utf-8")
    (repo / "spec" / "models" / "ship_spec.rb").write_text(
        "RSpec.describe Ship do\n  it 'works' do\n    expect(true).to eq(true)\n  end\nend\n",
        encoding="utf-8",
    )
    (repo / "private" / "search.test.ts").write_text("test('search', () => expect(true).toBe(true));\n", encoding="utf-8")
    (repo / "workspaces" / "chat" / "src" / "widget.tsx").write_text(
        "export function Widget() { return <div>widget</div>; }\n",
        encoding="utf-8",
    )
    (repo / "workspaces" / "chat" / "test" / "widget.test.tsx").write_text(
        "it('widget', () => expect(true).toBe(true));\n",
        encoding="utf-8",
    )
    return repo


def make_profileless_rust_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "rust-workspace-like"
    (repo / "codex-rs" / "tui" / "src" / "bottom_pane").mkdir(parents=True)
    (repo / "codex-rs" / "tui" / "tests").mkdir(parents=True)
    (repo / "AGENTS.md").write_text(
        "# Rust/codex-rs\n"
        "- When using format! and you can inline variables into {}, always do that.\n"
        "- Install any commands the repo relies on if they are missing.\n"
        "- Run `cargo test -p codex-tui` for TUI changes.\n"
        "- Run `just fix -p <project>` before finalizing larger Rust changes.\n"
        "- Run `just argument-comment-lint` to keep comment lint clean.\n",
        encoding="utf-8",
    )
    (repo / "justfile").write_text(
        "set working-directory := \"codex-rs\"\n"
        "fix *args:\n"
        "    cargo clippy --fix --tests --allow-dirty \"$@\"\n",
        encoding="utf-8",
    )
    (repo / "codex-rs" / "tui" / "Cargo.toml").write_text(
        "[package]\nname = \"codex-tui\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (repo / "codex-rs" / "tui" / "src" / "bottom_pane" / "chat_composer.rs").write_text(
        "pub fn render() {}\n",
        encoding="utf-8",
    )
    (repo / "codex-rs" / "tui" / "tests" / "chat_composer.rs").write_text(
        "#[test]\nfn chat_composer() {}\n",
        encoding="utf-8",
    )
    return repo


def make_profileless_js_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "ops-ui-like"
    (repo / "ui" / "app" / "src" / "pages" / "__tests__").mkdir(parents=True)
    (repo / "AGENTS.md").write_text(
        "# Assistant Context Bootstrap\n"
        "- Use `POST /v1/context/compile` to assemble a compact working context.\n"
        "- Treat this file as broad human handoff context.\n"
        "\n"
        "# Frontend Structure\n"
        "- Legacy/debug UI remains under `/legacy/*` and should be treated as compatibility tooling.\n"
        "\n"
        "# Current Preferred Workflow for Gameplay Features\n"
        "1. fix or add backend service logic\n"
        "2. expose it through a typed router if needed\n"
        "3. wire the new UI route/component to the typed API\n"
        "\n"
        "# Current Preferred Workflow for Debugging\n"
        "- If the dashboard looks wrong, check whether the UI is using stale data in `DashboardPage.tsx`.\n"
        "- Do not assume git status works in every environment.\n",
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        "test-ui:\n"
        "\tcd ui/app && npm test\n",
        encoding="utf-8",
    )
    (repo / "ui" / "app" / "package.json").write_text(
        '{\n'
        '  "name": "ops-control-ui",\n'
        '  "private": true,\n'
        '  "scripts": {\n'
        '    "build": "vite build",\n'
        '    "test": "vitest run"\n'
        "  },\n"
        '  "packageManager": "pnpm@10.17.0"\n'
        "}\n",
        encoding="utf-8",
    )
    (repo / "ui" / "app" / "src" / "pages" / "DashboardPage.tsx").write_text(
        "export function DashboardPage() { return null; }\n",
        encoding="utf-8",
    )
    (repo / "ui" / "app" / "src" / "pages" / "__tests__" / "DashboardPage.test.tsx").write_text(
        "it('DashboardPage', () => expect(true).toBe(true));\n",
        encoding="utf-8",
    )
    return repo


def test_repo_aware_prompts_are_treated_as_capture_requests() -> None:
    assert is_research_request("What exact command should I run for workspaces/chat/src/widget.tsx?")
    assert is_research_request("Review this change in app/models/ship.rb for reviewer concerns.")


def test_repo_capture_request_routes_workspace_files_to_workspace_area(tmp_path: Path) -> None:
    repo = make_monolith_repo(tmp_path)

    request = resolve_repo_capture_request(
        "What exact command should I run for workspaces/chat/src/widget.tsx?",
        source_roots=[repo],
    )

    assert request is not None
    assert request.mode == "repo_triage"
    assert request.primary_area is not None
    assert request.primary_area.name == "workspace-ui"
    assert request.target_paths == ["workspaces/chat/src/widget.tsx"]
    assert request.focus.domain == "repo-intelligence"


def test_repo_capture_uses_nearest_agents_and_scoped_ruby_command(tmp_path: Path) -> None:
    repo = make_monolith_repo(tmp_path)
    request = resolve_repo_capture_request(
        "What exact command should I run for spec/models/ship_spec.rb?",
        source_roots=[repo],
    )

    assert request is not None
    result = run_repo_capture("What exact command should I run for spec/models/ship_spec.rb?", request)

    assert result.matched_area == "ruby-app"
    assert result.commands[0].command == "script/spring --non-interactive rspec spec/models/ship_spec.rb"
    assert any(instruction.path == "AGENTS.md" for instruction in result.instructions)
    assert any(instruction.path == "spec/AGENTS.md" for instruction in result.instructions)
    assert "## Commands Recommended" in result.report_md
    assert "script/spring --non-interactive rspec spec/models/ship_spec.rb" in result.report_md


def test_repo_capture_surfaces_missing_private_node_modules_as_blocker(tmp_path: Path) -> None:
    repo = make_monolith_repo(tmp_path)
    request = resolve_repo_capture_request(
        "Why is private/search.test.ts failing in this repo?",
        source_roots=[repo],
    )

    assert request is not None
    result = run_repo_capture("Why is private/search.test.ts failing in this repo?", request)

    assert result.matched_area == "private-js"
    assert any(check.status == "blocker" and "private/node_modules is missing" in check.detail for check in result.preflight)
    assert any("environment" in need.lower() or "missing" in need.lower() for need in result.guidance.needs)


def test_repo_capture_review_mode_adds_workspace_reviewer_notes(tmp_path: Path) -> None:
    repo = make_monolith_repo(tmp_path)
    prompt = "Review this change in workspaces/chat/src/widget.tsx and call out likely reviewer concerns."
    request = resolve_repo_capture_request(prompt, source_roots=[repo])

    assert request is not None
    result = run_repo_capture(prompt, request)

    assert result.mode == "repo_review"
    assert result.matched_area == "workspace-ui"
    assert "## Reviewer Notes" in result.report_md
    assert "Mention the owning workspace in reviewer notes." in result.report_md


def test_profileless_rust_repo_infers_crate_commands(tmp_path: Path) -> None:
    repo = make_profileless_rust_repo(tmp_path)
    prompt = "What exact command should I run for codex-rs/tui/src/bottom_pane/chat_composer.rs?"
    request = resolve_repo_capture_request(prompt, source_roots=[repo])

    assert request is not None
    assert request.primary_area is not None
    assert request.primary_area.name == "codex-tui"

    result = run_repo_capture(prompt, request)

    assert result.matched_area == "codex-tui"
    assert result.commands[0].command == "cargo test -p codex-tui"
    assert any(command.command == "just fix -p codex-tui" for command in result.commands)
    assert any("AGENTS.md" == instruction.path for instruction in result.instructions)
    assert any("cargo test -p codex-tui" in instruction.summary for instruction in result.instructions)
    assert all("Install any commands" not in instruction.summary for instruction in result.instructions)


def test_profileless_js_repo_infers_package_commands_and_test_target(tmp_path: Path) -> None:
    repo = make_profileless_js_repo(tmp_path)
    prompt = "What exact command should I run for ui/app/src/pages/DashboardPage.tsx?"
    request = resolve_repo_capture_request(prompt, source_roots=[repo])

    assert request is not None
    assert request.primary_area is not None
    assert request.primary_area.name == "ops-control-ui"

    result = run_repo_capture(prompt, request)

    assert result.matched_area == "ops-control-ui"
    assert result.commands[0].command == "pnpm --dir ui/app test -- ui/app/src/pages/__tests__/DashboardPage.test.tsx"
    assert any(check.status == "blocker" and "ui/app/node_modules is missing" in check.detail for check in result.preflight)
    assert any("DashboardPage.tsx" in instruction.summary for instruction in result.instructions)
    assert all("context/compile" not in instruction.summary for instruction in result.instructions)


def test_implicit_capture_stores_repo_triage_session(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    repo = make_monolith_repo(tmp_path)

    outcome = run_implicit_research_capture(
        "What exact command should I run for workspaces/chat/src/widget.tsx?",
        backend=service,
        source_roots=[repo],
    )

    assert outcome.specialized_domain == "repo-intelligence"
    assert outcome.specialist_mode == "repo_triage"
    assert outcome.capture_summary.stored_report_id is not None
    assert outcome.capture_summary.stored_claim_ids
    assert outcome.summary_contract_passed is True
    assert "## Affected Area" in (outcome.narrative_summary_md or "")
    session = service.get_session(outcome.capture_summary.stored_session_id, include_private=True)
    assert session.mode == "repo_triage"


def test_implicit_capture_stores_repo_review_session(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    repo = make_monolith_repo(tmp_path)

    outcome = run_implicit_research_capture(
        "Review this change in workspaces/chat/src/widget.tsx and call out likely reviewer concerns.",
        backend=service,
        source_roots=[repo],
    )

    assert outcome.specialized_domain == "repo-intelligence"
    assert outcome.specialist_mode == "repo_review"
    assert outcome.capture_summary.stored_report_id is not None
    assert outcome.summary_contract_passed is True
    session = service.get_session(outcome.capture_summary.stored_session_id, include_private=True)
    assert session.mode == "repo_review"
