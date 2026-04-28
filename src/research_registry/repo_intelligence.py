from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tomllib
from typing import Literal

from pydantic import BaseModel, Field

from .local_research import (
    LocalClaimDraft,
    LocalFollowUpDraft,
    LocalGuidanceDraft,
    extract_context,
    infer_source_type,
    read_text_lines,
    run_python_scan,
    run_rg,
    short_source_title,
)
from .models import FocusTuple, SourceCreate, SourceSelector

RepoCaptureMode = Literal["repo_triage", "repo_review"]
CheckStatus = Literal["ok", "warning", "blocker"]

REPO_REVIEW_PATTERNS = (
    r"\breview\b",
    r"\breviewer\b",
    r"\brisk area\b",
    r"\breviewer concerns?\b",
    r"\bgerrit\b",
)
REPO_TRIAGE_PATTERNS = (
    r"\bwhat exact command\b",
    r"\bwhat command should i run\b",
    r"\bfailing (?:test|spec|build|lint)\b",
    r"\bstack trace\b",
    r"\berror log\b",
    r"\bwhy is this failing\b",
    r"\bwhich command\b",
    r"\bpreflight\b",
    r"\bcoverage\b",
    r"\bin this repo\b",
    r"\bfor this file\b",
    r"\bAGENTS\.md\b",
)
PATH_TOKEN_RE = re.compile(r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\:\d+)?)")
FILE_TOKEN_RE = re.compile(r"(?P<file>[A-Za-z0-9_.-]+\.(?:py|rb|ts|tsx|js|jsx|go|rs|java|kt|sh|yml|yaml|json|md))(?:\:\d+)?")
STACK_TRACE_PATH_RE = re.compile(r"(?:File |from |at )(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_./-]+)(?::(?P<line>\d+))?")
INSTRUCTION_KEYWORDS = {
    "repo_triage": (
        "test",
        "spec",
        "command",
        "run",
        "debug",
        "failing",
        "stack",
        "trace",
        "lint",
        "build",
        "coverage",
        "non-interactive",
        "spring",
        "docker",
        "timeout",
        "setup",
        "guardrail",
        "do not",
        "never",
        "prefer",
        "avoid",
    ),
    "repo_review": (
        "review",
        "reviewer",
        "concern",
        "risk",
        "test",
        "coverage",
        "docs",
        "owner",
        "gerrit",
        "guardrail",
        "do not",
        "never",
        "prefer",
        "avoid",
    ),
}
CONFIG_NAMES = (
    "pyproject.toml",
    "pytest.ini",
    "Makefile",
    "package.json",
    "vitest.config.ts",
    "vitest.config.js",
    "jest.config.ts",
    "jest.config.js",
    "Gemfile",
    ".rubocop.yml",
    "rubocop.yml",
    "docker-compose.yml",
    "compose.yaml",
)


class RepoProfileRepo(BaseModel):
    name: str
    default_test_command: str | None = None
    default_lint_command: str | None = None
    default_build_command: str | None = None


class RepoProfilePolicy(BaseModel):
    prefer_non_interactive: bool = True
    forbid_destructive_git: bool = True
    no_full_repo_tests: bool = True
    long_timeout_commands: list[str] = Field(default_factory=list)
    review_conventions: list[str] = Field(default_factory=list)


class RepoProfileSetup(BaseModel):
    required_tools: list[str] = Field(default_factory=list)
    required_paths: list[str] = Field(default_factory=list)
    coverage_paths: list[str] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)
    startup_notes: list[str] = Field(default_factory=list)


class RepoProfileArea(BaseModel):
    name: str
    globs: list[str] = Field(default_factory=list)
    test_command: str | None = None
    lint_command: str | None = None
    build_command: str | None = None
    coverage_paths: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_paths: list[str] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    review_conventions: list[str] = Field(default_factory=list)
    stack_trace_hints: list[str] = Field(default_factory=list)


class RepoProfile(BaseModel):
    repo: RepoProfileRepo
    policy: RepoProfilePolicy = Field(default_factory=RepoProfilePolicy)
    setup: RepoProfileSetup = Field(default_factory=RepoProfileSetup)
    areas: list[RepoProfileArea] = Field(default_factory=list)


class RepoInstruction(BaseModel):
    path: str
    line_number: int
    summary: str


class RepoCommandRecommendation(BaseModel):
    kind: str
    command: str
    rationale: str


class RepoCheck(BaseModel):
    status: CheckStatus
    label: str
    detail: str


class RepoEvidenceHit(BaseModel):
    source: SourceCreate
    selector: SourceSelector
    quote_text: str
    note: str
    matched_terms: list[str] = Field(default_factory=list)
    score: float = 0.0
    repo_name: str
    file_path: str
    category: str


class RepoCaptureRequest(BaseModel):
    mode: RepoCaptureMode
    repo_root: str
    repo_name: str
    profile_path: str | None = None
    profile: RepoProfile | None = None
    target_paths: list[str] = Field(default_factory=list)
    primary_area: RepoProfileArea | None = None
    focus: FocusTuple


class RepoCaptureResult(BaseModel):
    mode: RepoCaptureMode
    focus: FocusTuple
    repo_root: str
    repo_name: str
    profile_path: str | None = None
    target_paths: list[str] = Field(default_factory=list)
    matched_area: str | None = None
    instructions: list[RepoInstruction] = Field(default_factory=list)
    commands: list[RepoCommandRecommendation] = Field(default_factory=list)
    preflight: list[RepoCheck] = Field(default_factory=list)
    hits: list[RepoEvidenceHit] = Field(default_factory=list)
    claim_drafts: list[LocalClaimDraft] = Field(default_factory=list)
    guidance: LocalGuidanceDraft = Field(default_factory=LocalGuidanceDraft)
    report_md: str


def resolve_repo_capture_request(prompt: str, *, source_roots: list[Path] | None = None) -> RepoCaptureRequest | None:
    repo_root = discover_repo_root(source_roots=source_roots)
    if repo_root is None:
        return None
    profile_path = repo_root / ".codex" / "repo-profile.toml"
    profile = load_repo_profile(profile_path) if profile_path.exists() else None
    if profile is None and not any(path.name == "AGENTS.md" for path in repo_root.rglob("AGENTS.md")):
        return None
    mode = classify_repo_prompt(prompt)
    if mode is None:
        return None
    target_paths = extract_target_paths(prompt, repo_root)
    if mode == "repo_review" and not target_paths:
        target_paths = collect_git_changed_paths(repo_root)
    primary_area = resolve_primary_area(profile, target_paths)
    focus = build_repo_focus(
        repo_name=profile.repo.name if profile else repo_root.name,
        mode=mode,
        primary_area=primary_area.name if primary_area else None,
        target_paths=target_paths,
    )
    return RepoCaptureRequest(
        mode=mode,
        repo_root=str(repo_root),
        repo_name=profile.repo.name if profile else repo_root.name,
        profile_path=str(profile_path) if profile_path.exists() else None,
        profile=profile,
        target_paths=target_paths,
        primary_area=primary_area,
        focus=focus,
    )


def run_repo_capture(prompt: str, request: RepoCaptureRequest) -> RepoCaptureResult:
    repo_root = Path(request.repo_root)
    instructions = resolve_instructions(repo_root, request.target_paths, request.mode)
    commands = build_command_recommendations(prompt, repo_root, request)
    preflight = evaluate_preflight(repo_root, request, commands)
    failure_bucket = classify_failure_bucket(prompt, preflight)
    evidence_hits = collect_repo_evidence(
        prompt=prompt,
        repo_root=repo_root,
        request=request,
        instructions=instructions,
        preflight=preflight,
        commands=commands,
    )
    guidance = build_repo_guidance(
        request=request,
        instructions=instructions,
        commands=commands,
        preflight=preflight,
        failure_bucket=failure_bucket,
        evidence_hits=evidence_hits,
    )
    claim_drafts = build_repo_claim_drafts(
        request=request,
        commands=commands,
        preflight=preflight,
        guidance=guidance,
        failure_bucket=failure_bucket,
        evidence_hits=evidence_hits,
    )
    report_md = render_repo_report(
        prompt=prompt,
        request=request,
        instructions=instructions,
        commands=commands,
        preflight=preflight,
        guidance=guidance,
        failure_bucket=failure_bucket,
    )
    return RepoCaptureResult(
        mode=request.mode,
        focus=request.focus,
        repo_root=str(repo_root),
        repo_name=request.repo_name,
        profile_path=request.profile_path,
        target_paths=request.target_paths,
        matched_area=request.primary_area.name if request.primary_area else None,
        instructions=instructions,
        commands=commands,
        preflight=preflight,
        hits=evidence_hits,
        claim_drafts=claim_drafts,
        guidance=guidance,
        report_md=report_md,
    )


def evaluate_repo_summary_contract(summary_md: str, *, commands: list[RepoCommandRecommendation], instructions: list[RepoInstruction], registry_ids: list[str]) -> bool:
    required_sections = (
        "## Affected Area",
        "## Instructions Found",
        "## Evidence Checked",
        "## Commands Recommended",
        "## Blockers",
        "## Likely Hypotheses",
        "## Reviewer Notes",
        "## Coverage Follow-up",
        "## Registry State",
    )
    if not all(section in summary_md for section in required_sections):
        return False
    if commands and not any(command.command in summary_md for command in commands[:2]):
        return False
    if instructions and not any(instruction.path in summary_md for instruction in instructions[:2]):
        return False
    if registry_ids and not all(registry_id in summary_md for registry_id in registry_ids[:2]):
        return False
    return True


def discover_repo_root(*, source_roots: list[Path] | None = None) -> Path | None:
    candidates = source_roots or [Path.cwd()]
    for candidate in candidates:
        current = candidate.resolve()
        if current.is_file():
            current = current.parent
        for directory in [current, *current.parents]:
            if (directory / ".codex" / "repo-profile.toml").exists():
                return directory
            if (directory / ".git").exists():
                return directory
            if (directory / "AGENTS.md").exists():
                return directory
    return None


def load_repo_profile(path: Path) -> RepoProfile | None:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return RepoProfile.model_validate(raw)


def classify_repo_prompt(prompt: str) -> RepoCaptureMode | None:
    normalized = " ".join(prompt.lower().split())
    if any(re.search(pattern, normalized) for pattern in REPO_REVIEW_PATTERNS):
        return "repo_review"
    if any(re.search(pattern, normalized) for pattern in REPO_TRIAGE_PATTERNS):
        return "repo_triage"
    if PATH_TOKEN_RE.search(prompt) or STACK_TRACE_PATH_RE.search(prompt):
        return "repo_triage"
    return None


def resolve_primary_area(profile: RepoProfile | None, target_paths: list[str]) -> RepoProfileArea | None:
    if profile is None or not target_paths:
        return None
    scores: dict[str, tuple[int, int]] = {}
    areas = {area.name: area for area in profile.areas}
    for area in profile.areas:
        matched = [path for path in target_paths if any(path_matches_glob(path, pattern) for pattern in area.globs)]
        if matched:
            specificity = max(len(pattern) for pattern in area.globs)
            scores[area.name] = (len(matched), specificity)
    if not scores:
        return None
    winner = max(scores.items(), key=lambda item: item[1])[0]
    return areas[winner]


def build_repo_focus(*, repo_name: str, mode: RepoCaptureMode, primary_area: str | None, target_paths: list[str]) -> FocusTuple:
    concern = "review guidance" if mode == "repo_review" else "failure triage and command routing"
    constraint = ", ".join(target_paths[:2]) if target_paths else None
    return FocusTuple(
        domain="repo-intelligence",
        object=primary_area or "repo workflow",
        concern=concern,
        context=repo_name,
        constraint=constraint,
    )


def extract_target_paths(prompt: str, repo_root: Path) -> list[str]:
    raw_candidates: list[str] = []
    for pattern in (PATH_TOKEN_RE, STACK_TRACE_PATH_RE):
        for match in pattern.finditer(prompt):
            raw = match.group("path")
            if raw:
                raw_candidates.append(raw)
    for match in FILE_TOKEN_RE.finditer(prompt):
        raw_candidates.append(match.group("file"))
    normalized: list[str] = []
    for raw in raw_candidates:
        cleaned = raw.rstrip(".,);]}'\"")
        cleaned = cleaned.split(":", 1)[0]
        path = Path(cleaned)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(repo_root)
            except ValueError:
                continue
            candidate = rel.as_posix()
        else:
            candidate = cleaned.lstrip("./")
            candidate_path = repo_root / candidate
            if not candidate_path.exists():
                basename_matches = list(repo_root.rglob(Path(candidate).name))
                if len(basename_matches) == 1:
                    candidate = basename_matches[0].relative_to(repo_root).as_posix()
                else:
                    continue
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized[:8]


def collect_git_changed_paths(repo_root: Path) -> list[str]:
    output = run_command(["git", "-C", str(repo_root), "status", "--porcelain"], allow_failure=True)
    if not output:
        return []
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return paths[:12]


def resolve_instructions(repo_root: Path, target_paths: list[str], mode: RepoCaptureMode) -> list[RepoInstruction]:
    candidates: list[Path] = []
    if not target_paths:
        root_agents = repo_root / "AGENTS.md"
        if root_agents.exists():
            candidates.append(root_agents)
    for target in target_paths or [""]:
        current = (repo_root / target).resolve()
        if current.is_file():
            current = current.parent
        lineage = []
        for directory in [current, *current.parents]:
            try:
                directory.relative_to(repo_root)
            except ValueError:
                continue
            lineage.append(directory)
            if directory == repo_root:
                break
        for directory in reversed(lineage):
            agents_path = directory / "AGENTS.md"
            if agents_path.exists() and agents_path not in candidates:
                candidates.append(agents_path)
    instructions: list[RepoInstruction] = []
    seen = set()
    for path in candidates:
        instructions.extend(extract_relevant_instructions(path, repo_root=repo_root, mode=mode, seen=seen))
    return instructions[:16]


def extract_relevant_instructions(path: Path, *, repo_root: Path, mode: RepoCaptureMode, seen: set[tuple[str, int]]) -> list[RepoInstruction]:
    keywords = INSTRUCTION_KEYWORDS[mode]
    heading = ""
    extracted: list[RepoInstruction] = []
    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("# ").strip()
            continue
        lowered = stripped.lower()
        if not any(keyword in lowered for keyword in keywords) and not lowered.startswith(("- ", "* ", "1.", "2.", "3.")):
            continue
        if not any(keyword in lowered for keyword in keywords) and not any(token in lowered for token in ("never", "always", "prefer", "avoid", "do not", "must")):
            continue
        key = (str(path), index)
        if key in seen:
            continue
        seen.add(key)
        summary = stripped
        if heading:
            summary = f"{heading}: {summary}"
        extracted.append(
            RepoInstruction(
                path=path.relative_to(repo_root).as_posix(),
                line_number=index,
                summary=summary,
            )
        )
    return extracted


def build_command_recommendations(prompt: str, repo_root: Path, request: RepoCaptureRequest) -> list[RepoCommandRecommendation]:
    area = request.primary_area
    profile = request.profile
    target_paths = request.target_paths
    workspace = derive_workspace(target_paths)
    target = target_paths[0] if target_paths else ""
    test_targets = " ".join(resolve_test_targets(repo_root, target_paths))
    context = defaultdict(
        str,
        {
            "target": target,
            "test_targets": test_targets or target,
            "lint_targets": " ".join(target_paths) or target,
            "build_targets": " ".join(target_paths) or target,
            "workspace": workspace or "",
            "area": area.name if area else "",
        },
    )
    recommendations: list[RepoCommandRecommendation] = []
    if area and area.test_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="test",
                command=area.test_command.format_map(context).strip(),
                rationale=f"Scoped test command from repo profile area `{area.name}`.",
            )
        )
    elif profile and profile.repo.default_test_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="test",
                command=profile.repo.default_test_command.format_map(context).strip(),
                rationale="Repo-level default test command from the checked-in profile.",
            )
        )
    if area and area.lint_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="lint",
                command=area.lint_command.format_map(context).strip(),
                rationale=f"Scoped lint command from repo profile area `{area.name}`.",
            )
        )
    elif profile and profile.repo.default_lint_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="lint",
                command=profile.repo.default_lint_command.format_map(context).strip(),
                rationale="Repo-level default lint command from the checked-in profile.",
            )
        )
    if area and area.build_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="build",
                command=area.build_command.format_map(context).strip(),
                rationale=f"Scoped build command from repo profile area `{area.name}`.",
            )
        )
    elif profile and profile.repo.default_build_command and request.mode == "repo_review":
        recommendations.append(
            RepoCommandRecommendation(
                kind="build",
                command=profile.repo.default_build_command.format_map(context).strip(),
                rationale="Repo-level build command for review validation.",
            )
        )
    inspect_command = build_inspect_command(prompt, target_paths)
    if inspect_command:
        recommendations.append(
            RepoCommandRecommendation(
                kind="inspect",
                command=inspect_command,
                rationale="Targeted inspection command derived from the prompt and affected paths.",
            )
        )
    if request.mode == "repo_review" and target_paths:
        recommendations.append(
            RepoCommandRecommendation(
                kind="diff",
                command=f"git diff -- {' '.join(target_paths)}",
                rationale="Review-specific diff scoped to the affected paths.",
            )
        )
    deduped: list[RepoCommandRecommendation] = []
    seen = set()
    for item in recommendations:
        if not item.command or item.command in seen:
            continue
        seen.add(item.command)
        deduped.append(item)
    return deduped[:4]


def evaluate_preflight(repo_root: Path, request: RepoCaptureRequest, commands: list[RepoCommandRecommendation]) -> list[RepoCheck]:
    profile = request.profile
    area = request.primary_area
    checks: list[RepoCheck] = []
    required_tools = unique_items(
        (profile.setup.required_tools if profile else [])
        + (area.required_tools if area else [])
    )
    for tool in required_tools:
        checks.append(
            RepoCheck(
                status="ok" if shutil.which(tool) else "blocker",
                label=f"tool:{tool}",
                detail=f"{tool} {'is available' if shutil.which(tool) else 'is missing from PATH'}.",
            )
        )
    required_paths = unique_items(
        (profile.setup.required_paths if profile else [])
        + (area.required_paths if area else [])
    )
    for relative_path in required_paths:
        candidate = repo_root / relative_path
        checks.append(
            RepoCheck(
                status="ok" if candidate.exists() else "blocker",
                label=f"path:{relative_path}",
                detail=f"{relative_path} {'exists' if candidate.exists() else 'is missing'} under the repo root.",
            )
        )
    coverage_paths = unique_items(
        (area.coverage_paths if area and area.coverage_paths else [])
        + (profile.setup.coverage_paths if profile else [])
    )
    if coverage_paths:
        existing = [path for path in coverage_paths if (repo_root / path).exists()]
        checks.append(
            RepoCheck(
                status="ok" if existing else "warning",
                label="coverage",
                detail=f"Coverage artifacts present at {', '.join(existing)}." if existing else f"No coverage artifacts found at {', '.join(coverage_paths)}.",
            )
        )
    for service in unique_items((profile.setup.required_services if profile else []) + (area.required_services if area else [])):
        checks.append(
            RepoCheck(
                status="warning",
                label=f"service:{service}",
                detail=f"Verify the required service `{service}` is running before executing scoped commands.",
            )
        )
    for note in profile.setup.startup_notes if profile else []:
        checks.append(RepoCheck(status="warning", label="startup", detail=note))
    if profile and profile.policy.no_full_repo_tests:
        checks.append(RepoCheck(status="ok", label="policy:no-full-suite", detail="The repo profile forbids broad full-repo test runs by default."))
    if profile and profile.policy.prefer_non_interactive:
        checks.append(RepoCheck(status="ok", label="policy:non-interactive", detail="Prefer non-interactive command forms in this repo."))
    if profile and profile.policy.forbid_destructive_git:
        checks.append(RepoCheck(status="ok", label="policy:git-safety", detail="Do not use destructive git commands in this repo."))
    long_timeout_commands = profile.policy.long_timeout_commands if profile else []
    if long_timeout_commands:
        checks.append(
            RepoCheck(
                status="warning",
                label="timeouts",
                detail=f"Expect long startup or execution time for: {', '.join(long_timeout_commands)}.",
            )
        )
    if commands and not any(check.status == "blocker" for check in checks):
        checks.append(RepoCheck(status="ok", label="commands", detail=f"Scoped command set is ready: {', '.join(command.kind for command in commands[:3])}."))
    return checks


def classify_failure_bucket(prompt: str, preflight: list[RepoCheck]) -> str:
    normalized = prompt.lower()
    if any(check.status == "blocker" for check in preflight):
        return "environment"
    if any(token in normalized for token in ("fixture", "factory", "snapshot", "seed data")):
        return "fixture-data"
    if any(token in normalized for token in ("generated", "codegen", "lockfile", "schema drift")):
        return "stale-generated-state"
    if any(token in normalized for token in ("timeout", "flaky", "retry", "race")):
        return "flaky-timeout"
    if any(token in normalized for token in ("stack trace", "assertion", "expected", "undefined method", "typeerror", "nameerror", "failing")):
        return "code"
    return "instruction-gap"


def collect_repo_evidence(
    *,
    prompt: str,
    repo_root: Path,
    request: RepoCaptureRequest,
    instructions: list[RepoInstruction],
    preflight: list[RepoCheck],
    commands: list[RepoCommandRecommendation],
) -> list[RepoEvidenceHit]:
    hits: list[RepoEvidenceHit] = []
    if request.profile_path:
        profile_path = Path(request.profile_path)
        lines = read_text_lines(str(profile_path))
        snippet = "\n".join(lines[:12]).strip()
        hits.append(
            build_file_hit(
                repo_root=repo_root,
                path=profile_path,
                quote_text=snippet or profile_path.read_text(encoding="utf-8"),
                line_number=1,
                note="Checked the repo profile for scoped commands, setup, and policy guardrails.",
                matched_terms=["repo-profile", request.primary_area.name if request.primary_area else "profile"],
                category="profile",
            )
        )
    for instruction in instructions[:10]:
        source_path = repo_root / instruction.path
        lines = read_text_lines(str(source_path))
        snippet, start, _ = extract_context(lines, instruction.line_number, radius=1)
        hits.append(
            build_file_hit(
                repo_root=repo_root,
                path=source_path,
                quote_text=snippet or instruction.summary,
                line_number=start,
                note=f"Resolved applicable instruction from {instruction.path}.",
                matched_terms=["AGENTS", request.mode],
                category="instruction",
            )
        )
    for config_path in discover_config_files(repo_root, request.target_paths):
        lines = read_text_lines(str(config_path))
        snippet, start = select_config_snippet(lines, prompt, commands)
        hits.append(
            build_file_hit(
                repo_root=repo_root,
                path=config_path,
                quote_text=snippet,
                line_number=start,
                note=f"Inspected {config_path.relative_to(repo_root).as_posix()} for local setup or command evidence.",
                matched_terms=["config", config_path.name],
                category="config",
            )
        )
    git_status = run_command(["git", "-C", str(repo_root), "status", "--short"], allow_failure=True)
    if git_status:
        hits.append(
            build_text_hit(
                repo_name=request.repo_name,
                locator="command://git-status",
                title=f"{request.repo_name}/git status",
                quote_text=git_status,
                note="Checked local git status for changed files and affected areas.",
                matched_terms=["git", "status"],
                category="git",
            )
        )
    if request.target_paths:
        git_log = run_command(["git", "-C", str(repo_root), "log", "--oneline", "-n", "5", "--", *request.target_paths], allow_failure=True)
        if git_log:
            hits.append(
                build_text_hit(
                    repo_name=request.repo_name,
                    locator="command://git-log",
                    title=f"{request.repo_name}/git log",
                    quote_text=git_log,
                    note="Checked recent git history for the affected paths.",
                    matched_terms=["git", "history"],
                    category="git",
                )
            )
    for check in preflight[:8]:
        hits.append(
            build_text_hit(
                repo_name=request.repo_name,
                locator=f"command://preflight/{check.label}",
                title=f"{request.repo_name}/preflight/{check.label}",
                quote_text=check.detail,
                note="Recorded repo preflight evidence for setup and guardrails.",
                matched_terms=["preflight", check.label],
                category="preflight",
            )
        )
    for rg_hit in collect_rg_hits(prompt, repo_root, request):
        hits.append(rg_hit)
    deduped: list[RepoEvidenceHit] = []
    seen = set()
    for hit in hits:
        key = (hit.source.locator, hit.selector.start_line or 0, hit.category)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped[:12]


def build_repo_guidance(
    *,
    request: RepoCaptureRequest,
    instructions: list[RepoInstruction],
    commands: list[RepoCommandRecommendation],
    preflight: list[RepoCheck],
    failure_bucket: str,
    evidence_hits: list[RepoEvidenceHit],
) -> LocalGuidanceDraft:
    current_guidance: list[str] = []
    if commands:
        current_guidance.append(f"Use the scoped `{commands[0].command}` command first for {request.primary_area.name if request.primary_area else 'this repo area'}.")
    if request.profile and request.profile.policy.no_full_repo_tests:
        current_guidance.append("Avoid broad repo-wide test commands unless a repo instruction explicitly requires them.")
    if request.profile and request.profile.policy.prefer_non_interactive:
        current_guidance.append("Prefer non-interactive command forms and scoped targets to keep repo automation predictable.")
    if request.mode == "repo_review":
        current_guidance.append("Review this change against the nearest area instructions, not only the root repo guidance.")
    evidence_now = []
    for hit in evidence_hits[:6]:
        line = hit.selector.start_line or 1
        evidence_now.append(f"{short_source_title(hit.file_path, hit.repo_name)}:{line} [{hit.category}] {hit.note}")
    blockers = [check.detail for check in preflight if check.status == "blocker"]
    warnings = [check.detail for check in preflight if check.status == "warning"]
    gaps: list[str] = []
    if not instructions:
        gaps.append("No applicable AGENTS.md instructions were resolved for the affected path.")
    if not commands:
        gaps.append("No scoped repo command was found for the affected path or workflow.")
    if not any(hit.category == "config" for hit in evidence_hits):
        gaps.append("Relevant local manifests or config files were thin, so command evidence is partial.")
    needs = blockers or [f"Need a clearer root-cause check for the `{failure_bucket}` bucket before broadening the debug pass."] if failure_bucket == "instruction-gap" else blockers
    wants = warnings or ["Want explicit coverage artifacts or reviewer notes for this area before reusing the result broadly."]
    follow_ups = build_repo_follow_ups(request=request, blockers=blockers, gaps=gaps, warnings=warnings)
    return LocalGuidanceDraft(
        current_guidance=current_guidance,
        evidence_now=evidence_now,
        gaps=gaps,
        needs=needs,
        wants=wants,
        follow_ups=follow_ups,
    )


def build_repo_claim_drafts(
    *,
    request: RepoCaptureRequest,
    commands: list[RepoCommandRecommendation],
    preflight: list[RepoCheck],
    guidance: LocalGuidanceDraft,
    failure_bucket: str,
    evidence_hits: list[RepoEvidenceHit],
) -> list[LocalClaimDraft]:
    if not evidence_hits:
        return []
    drafts: list[LocalClaimDraft] = []
    primary_indexes = list(range(min(3, len(evidence_hits))))
    if commands:
        drafts.append(
            LocalClaimDraft(
                title=f"{request.primary_area.name if request.primary_area else 'Repo'} has a scoped execution path",
                statement=f"The checked-in repo profile and local instructions support running `{commands[0].command}` instead of a broad default command.",
                excerpt_indexes=primary_indexes,
                confidence=0.82,
            )
        )
    blocker_indexes = [index for index, hit in enumerate(evidence_hits) if hit.category == "preflight"][:3]
    blockers = [check for check in preflight if check.status == "blocker"]
    if blockers and blocker_indexes:
        drafts.append(
            LocalClaimDraft(
                title="Current failure risk is environmental before it is code-level",
                statement=f"Preflight blockers point to an environment/setup issue first, which means the `{failure_bucket}` signal should not be treated as a pure code defect yet.",
                excerpt_indexes=blocker_indexes,
                confidence=0.76,
            )
        )
    if request.mode == "repo_review":
        review_indexes = [index for index, hit in enumerate(evidence_hits) if hit.category in {"instruction", "config", "git"}][:3] or primary_indexes
        drafts.append(
            LocalClaimDraft(
                title="Reviewer attention will cluster around scoped validation and coverage",
                statement="The affected area has explicit command and review guidance, so the most likely reviewer concern is whether the change stayed scoped and kept adequate validation coverage.",
                excerpt_indexes=review_indexes,
                confidence=0.74,
            )
        )
    if not drafts:
        drafts.append(
            LocalClaimDraft(
                title="Repo guidance is still incomplete for this prompt",
                statement="The current pass found enough local evidence to store a triage record, but not enough command or instruction evidence to treat the result as fully mature guidance yet.",
                excerpt_indexes=primary_indexes,
                confidence=0.68,
            )
        )
    return drafts[:3]


def render_repo_report(
    *,
    prompt: str,
    request: RepoCaptureRequest,
    instructions: list[RepoInstruction],
    commands: list[RepoCommandRecommendation],
    preflight: list[RepoCheck],
    guidance: LocalGuidanceDraft,
    failure_bucket: str,
) -> str:
    blockers = [check.detail for check in preflight if check.status == "blocker"]
    warnings = [check.detail for check in preflight if check.status == "warning"]
    reviewer_notes = build_reviewer_notes(request=request, instructions=instructions)
    coverage_notes = build_coverage_follow_up(request=request, preflight=preflight)
    hypotheses = build_hypotheses(request=request, failure_bucket=failure_bucket, preflight=preflight)
    lines = [
        f"# {prompt}",
        "",
        "## Affected Area",
        f"- Repo: {request.repo_name}",
        f"- Mode: {request.mode}",
        f"- Area: {request.primary_area.name if request.primary_area else 'unmatched'}",
        f"- Targets: {', '.join(request.target_paths) if request.target_paths else 'none detected'}",
        "",
        "## Instructions Found",
    ]
    if instructions:
        lines.extend(f"- {instruction.path}:{instruction.line_number} {instruction.summary}" for instruction in instructions[:8])
    else:
        lines.append("- No applicable AGENTS.md instructions were resolved for this pass.")
    lines.extend(["", "## Evidence Checked"])
    if guidance.evidence_now:
        lines.extend(f"- {item}" for item in guidance.evidence_now)
    else:
        lines.append("- No local evidence artifacts were captured.")
    lines.extend(["", "## Commands Recommended"])
    if commands:
        lines.extend(f"{index}. [{command.kind}] `{command.command}` — {command.rationale}" for index, command in enumerate(commands[:4], start=1))
    else:
        lines.append("1. No scoped repo command was found.")
    lines.extend(["", "## Blockers"])
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- No blocking setup gaps were detected in this pass.")
    lines.extend(["", "## Likely Hypotheses"])
    lines.extend(f"- {item}" for item in hypotheses)
    lines.extend(["", "## Reviewer Notes"])
    if reviewer_notes:
        lines.extend(f"- {item}" for item in reviewer_notes)
    else:
        lines.append("- No additional reviewer-specific notes were derived beyond the scoped commands and guardrails.")
    lines.extend(["", "## Coverage Follow-up"])
    if coverage_notes:
        lines.extend(f"- {item}" for item in coverage_notes)
    else:
        lines.append("- No explicit coverage follow-up was identified.")
    lines.extend(
        [
            "",
            "## Registry State",
            f"- Focus label: {request.focus.label}",
            f"- Context: {request.focus.context}",
            f"- Failure bucket: {failure_bucket}",
            f"- Warnings: {', '.join(warnings) if warnings else 'none'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_reviewer_notes(*, request: RepoCaptureRequest, instructions: list[RepoInstruction]) -> list[str]:
    notes: list[str] = []
    if request.profile:
        notes.extend(request.profile.policy.review_conventions)
    if request.primary_area:
        notes.extend(request.primary_area.review_conventions)
        if request.primary_area.owners:
            notes.append(f"Likely owning area: {', '.join(request.primary_area.owners)}.")
    notes.extend(instruction.summary for instruction in instructions if "review" in instruction.summary.lower() or "test" in instruction.summary.lower())
    deduped: list[str] = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped[:6]


def build_coverage_follow_up(*, request: RepoCaptureRequest, preflight: list[RepoCheck]) -> list[str]:
    notes = [check.detail for check in preflight if check.label == "coverage" and check.status != "ok"]
    if request.primary_area and request.primary_area.coverage_paths:
        notes.append(f"Watch coverage artifacts for {request.primary_area.name}: {', '.join(request.primary_area.coverage_paths)}.")
    return unique_items(notes)[:4]


def build_hypotheses(*, request: RepoCaptureRequest, failure_bucket: str, preflight: list[RepoCheck]) -> list[str]:
    area = request.primary_area.name if request.primary_area else "the unmatched area"
    blockers = [check.detail for check in preflight if check.status == "blocker"]
    if failure_bucket == "environment":
        return [
            f"The first failure mode is environmental because preflight blockers remain for {area}.",
            blockers[0] if blockers else "Missing setup is more likely than a product-code regression.",
        ]
    if failure_bucket == "fixture-data":
        return [
            f"The failure likely depends on fixture or snapshot state inside {area}.",
            "Verify seed data, factories, or snapshots before widening the investigation.",
        ]
    if failure_bucket == "stale-generated-state":
        return [
            f"The affected path in {area} looks sensitive to generated files, schemas, or lockfile drift.",
            "Regenerate the local derived artifacts before assuming a deeper code regression.",
        ]
    if failure_bucket == "flaky-timeout":
        return [
            f"The prompt reads like a timing or startup issue in {area}, not a deterministic logic bug.",
            "Re-run the scoped command with the repo's long-timeout guidance before changing code.",
        ]
    if failure_bucket == "instruction-gap":
        return [
            f"The local instruction and profile surface is thin for {area}.",
            "The next best move is to tighten repo guidance or area config before automating wider flows.",
        ]
    return [
        f"The failure most likely sits in the scoped code path for {area}.",
        "Use the first scoped command and targeted file search before broadening to repo-wide validation.",
    ]


def build_repo_follow_ups(
    *,
    request: RepoCaptureRequest,
    blockers: list[str],
    gaps: list[str],
    warnings: list[str],
) -> list[LocalFollowUpDraft]:
    follow_ups: list[LocalFollowUpDraft] = []
    area = request.primary_area.name if request.primary_area else "this repo area"
    for blocker in blockers[:2]:
        follow_ups.append(
            LocalFollowUpDraft(
                prompt=f"Research or document the setup fix for {area}: {blocker}",
                reason="need",
                rationale="A blocking local setup issue prevents confident scoped validation.",
                priority_score=0.96,
            )
        )
    for gap in gaps[:2]:
        follow_ups.append(
            LocalFollowUpDraft(
                prompt=f"Document repo guidance for {area}: {gap}",
                reason="gap",
                rationale=gap,
                priority_score=0.84,
            )
        )
    for warning in warnings[:1]:
        follow_ups.append(
            LocalFollowUpDraft(
                prompt=f"Add reusable evidence for {area}: {warning}",
                reason="want",
                rationale="Lower-priority improvements would make future triage more reusable.",
                priority_score=0.68,
            )
        )
    deduped: list[LocalFollowUpDraft] = []
    seen = set()
    for item in follow_ups:
        if item.prompt in seen:
            continue
        seen.add(item.prompt)
        deduped.append(item)
    return deduped[:5]


def discover_config_files(repo_root: Path, target_paths: list[str]) -> list[Path]:
    selected: list[Path] = []
    for name in CONFIG_NAMES:
        candidate = repo_root / name
        if candidate.exists():
            selected.append(candidate)
    for target in target_paths:
        target_path = (repo_root / target).resolve()
        if target_path.is_file():
            target_path = target_path.parent
        for directory in [target_path, *target_path.parents]:
            try:
                directory.relative_to(repo_root)
            except ValueError:
                continue
            for name in CONFIG_NAMES:
                candidate = directory / name
                if candidate.exists() and candidate not in selected:
                    selected.append(candidate)
            if directory == repo_root:
                break
    return selected[:10]


def select_config_snippet(lines: list[str], prompt: str, commands: list[RepoCommandRecommendation]) -> tuple[str, int]:
    terms = extract_search_terms(prompt) + [command.kind for command in commands]
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        if any(term in lowered for term in terms if term):
            snippet, start, _ = extract_context(lines, index, radius=1)
            return snippet or line.strip(), start
    for index, line in enumerate(lines, start=1):
        if line.strip():
            snippet, start, _ = extract_context(lines, index, radius=1)
            return snippet or line.strip(), start
    return "", 1


def collect_rg_hits(prompt: str, repo_root: Path, request: RepoCaptureRequest) -> list[RepoEvidenceHit]:
    terms = extract_search_terms(prompt)
    search_root = repo_root / request.target_paths[0] if request.target_paths and (repo_root / request.target_paths[0]).exists() else repo_root
    hits: list[RepoEvidenceHit] = []
    seen = set()
    for term in terms[:4]:
        matches = run_rg(term, search_root) if shutil.which("rg") else run_python_scan(term, search_root)
        for match in matches[:2]:
            path = Path(str(match["path"]))
            key = (str(path), int(match["line"]))
            if key in seen:
                continue
            seen.add(key)
            lines = read_text_lines(str(path))
            snippet, start, _ = extract_context(lines, int(match["line"]), radius=1)
            hits.append(
                build_file_hit(
                    repo_root=repo_root,
                    path=path,
                    quote_text=snippet,
                    line_number=start,
                    note=f"Targeted local search matched `{term}` in {short_source_title(str(path), request.repo_name)}.",
                    matched_terms=[term],
                    category="rg",
                )
            )
            if len(hits) >= 4:
                return hits
    return hits


def build_file_hit(
    *,
    repo_root: Path,
    path: Path,
    quote_text: str,
    line_number: int,
    note: str,
    matched_terms: list[str],
    category: str,
) -> RepoEvidenceHit:
    repo_name = repo_root.name
    relative = path.relative_to(repo_root).as_posix() if path.is_relative_to(repo_root) else path.name
    return RepoEvidenceHit(
        source=SourceCreate(
            locator=str(path),
            title=f"{repo_name}/{relative}",
            source_type=infer_source_type(str(path)),
            snippet=quote_text.splitlines()[0][:240] if quote_text else relative,
            snapshot_present=True,
        ),
        selector=SourceSelector(
            exact=quote_text.splitlines()[0].strip() if quote_text else relative,
            deep_link=f"{path}#L{line_number}",
            start_line=line_number,
            end_line=line_number + max(0, len(quote_text.splitlines()) - 1),
        ),
        quote_text=quote_text,
        note=note,
        matched_terms=matched_terms,
        score=8.0 if category in {"profile", "instruction"} else 6.5,
        repo_name=repo_name,
        file_path=str(path),
        category=category,
    )


def build_text_hit(
    *,
    repo_name: str,
    locator: str,
    title: str,
    quote_text: str,
    note: str,
    matched_terms: list[str],
    category: str,
) -> RepoEvidenceHit:
    content_hash = sha256(quote_text.encode("utf-8")).hexdigest()
    return RepoEvidenceHit(
        source=SourceCreate(
            locator=locator,
            title=title,
            source_type="command-output",
            snippet=quote_text.splitlines()[0][:240] if quote_text else title,
            content_sha256=content_hash,
            snapshot_present=True,
        ),
        selector=SourceSelector(
            exact=quote_text.splitlines()[0].strip() if quote_text else title,
            start_line=1,
            end_line=max(1, len(quote_text.splitlines())),
        ),
        quote_text=quote_text,
        note=note,
        matched_terms=matched_terms,
        score=5.5,
        repo_name=repo_name,
        file_path=locator,
        category=category,
    )


def derive_workspace(target_paths: list[str]) -> str | None:
    for target in target_paths:
        match = re.match(r"(?:workspaces|packages)/([^/]+)/", target)
        if match:
            return match.group(1)
    return None


def resolve_test_targets(repo_root: Path, target_paths: list[str]) -> list[str]:
    if not target_paths:
        return []
    targets: list[str] = []
    for target in target_paths:
        if any(part in target for part in ("/tests/", "/spec/", "test_", "_test.", "_spec.")):
            targets.append(target)
            continue
        stem = Path(target).stem
        candidates = list(repo_root.rglob(f"*{stem}*"))
        for candidate in candidates:
            relative = candidate.relative_to(repo_root).as_posix()
            if any(part in relative for part in ("/tests/", "/spec/", "test_", "_test.", "_spec.")) and relative not in targets:
                targets.append(relative)
        if not targets:
            targets.append(target)
    return targets[:4]


def build_inspect_command(prompt: str, target_paths: list[str]) -> str | None:
    terms = extract_search_terms(prompt)
    if target_paths and terms:
        return f"rg -n \"{terms[0]}\" {' '.join(target_paths[:2])}"
    if target_paths:
        return f"git diff -- {' '.join(target_paths[:2])}"
    if terms:
        return f"rg -n \"{terms[0]}\" ."
    return None


def extract_search_terms(prompt: str) -> list[str]:
    words = [token for token in re.findall(r"[a-z0-9_./-]+", prompt.lower()) if len(token) >= 4]
    filtered = [
        word
        for word in words
        if word not in {"what", "exact", "command", "should", "this", "repo", "review", "file", "failing", "error", "stack", "trace"}
    ]
    deduped: list[str] = []
    for term in filtered:
        if term not in deduped:
            deduped.append(term)
    return deduped[:8]


def run_command(command: list[str], *, allow_failure: bool = False) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, check=not allow_failure, text=True, timeout=10)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or completed.stderr).strip()


def unique_items(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def path_matches_glob(path: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")
    variants = {normalized_pattern}
    while "**/" in normalized_pattern:
        normalized_pattern = normalized_pattern.replace("**/", "", 1)
        variants.add(normalized_pattern)
    return any(
        fnmatch.fnmatch(normalized_path, variant) or PurePosixPath(normalized_path).match(variant)
        for variant in variants
    )
