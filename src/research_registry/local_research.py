from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Literal

from pydantic import BaseModel, Field

from .models import ClaimStatus, FocusTuple, SourceCreate, SourceSelector

LEADING_RESEARCH_PHRASES = (
    "research ",
    "investigate ",
    "look into ",
    "compare ",
    "gather sources on ",
    "gather sources for ",
    "find sources for ",
    "survey ",
)

SEGMENT_SPLIT_RE = re.compile(r"\b(?:for|between|under|when|across|with|in|on|of|against|from)\b")
RELATIVE_CLAUSE_SPLIT_RE = re.compile(r"\b(?:that|which|who)\b")
GENERIC_HEADS = (
    "coverage gaps",
    "retrieval failure modes",
    "failure modes",
    "differences",
    "gate design",
    "validation patterns",
    "api and data-model patterns",
    "artifact schemas",
    "requirements",
)
GENERIC_TAIL_WORDS = {"strategy", "strategies", "design", "designs", "policy", "policies", "model", "models", "patterns", "requirements"}
GENERIC_QUERY_WORDS = {
    "adds",
    "allocating",
    "artifacts",
    "batch",
    "benchmark",
    "candidate",
    "candidates",
    "comparisons",
    "constraints",
    "continuity",
    "current",
    "design",
    "designs",
    "diagnosis",
    "differences",
    "evaluation",
    "history",
    "management",
    "patterns",
    "policies",
    "policy",
    "post-run",
    "official",
    "pipeline",
    "promoting",
    "public",
    "regression",
    "reporting",
    "requirements",
    "review",
    "runs",
    "strategies",
    "strategy",
    "subset",
    "systems",
    "suite",
    "useful",
}
CONSTRAINT_TERMS = {
    "cpu",
    "gpu",
    "budget",
    "local",
    "public",
    "release",
    "candidate",
    "overnight",
    "coding",
    "narrative",
    "branch-private",
    "branch",
    "private",
    "multi-session",
    "cross-session",
}
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "of",
    "with",
    "in",
    "to",
    "on",
    "under",
    "between",
    "when",
    "across",
    "research",
    "investigate",
    "compare",
    "gather",
    "sources",
    "what",
    "how",
    "why",
    "current",
    "public",
    "system",
    "systems",
    "llm",
    "llms",
}
RG_GLOBS = (
    "!dist/**",
    "!.git/**",
    "!node_modules/**",
    "!__pycache__/**",
    "!*.sqlite3",
    "!*.whl",
    "!*.tar.gz",
    "!*.jsonl",
    "!*.pyc",
)
TERM_MATCH_LIMIT = 200
MAX_SEARCH_FILE_SIZE_BYTES = 1_000_000
IGNORED_DIR_NAMES = {".git", "dist", "node_modules", "__pycache__"}
IGNORED_FILE_SUFFIXES = (".sqlite3", ".whl", ".jsonl", ".pyc", ".tar.gz")


class LocalEvidenceHit(BaseModel):
    source: SourceCreate
    selector: SourceSelector
    quote_text: str
    note: str
    matched_terms: list[str]
    score: float
    repo_name: str
    file_path: str


class LocalClaimDraft(BaseModel):
    title: str
    statement: str
    excerpt_indexes: list[int] = Field(min_length=1)
    status: ClaimStatus = "supported"
    confidence: float = 0.7


class LocalFollowUpDraft(BaseModel):
    prompt: str
    reason: Literal["gap", "need", "want"]
    rationale: str
    priority_score: float = Field(default=0.7, ge=0.0, le=1.0)


class LocalGuidanceDraft(BaseModel):
    current_guidance: list[str] = Field(default_factory=list)
    evidence_now: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    wants: list[str] = Field(default_factory=list)
    follow_ups: list[LocalFollowUpDraft] = Field(default_factory=list)


class LocalResearchResult(BaseModel):
    focus: FocusTuple
    query_terms: list[str]
    source_roots: list[str]
    hits: list[LocalEvidenceHit]
    claim_drafts: list[LocalClaimDraft]
    guidance: LocalGuidanceDraft = Field(default_factory=LocalGuidanceDraft)
    gaps: list[str] = Field(default_factory=list)
    report_md: str | None = None


def default_source_roots() -> dict[str, Path]:
    configured_roots = os.getenv("RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS", "").strip()
    if configured_roots:
        roots: dict[str, Path] = {}
        for index, raw_path in enumerate(configured_roots.split(os.pathsep), start=1):
            path = Path(raw_path).expanduser().resolve()
            if not path.exists():
                continue
            name = path.name or f"root-{index}"
            if name in roots:
                name = f"{name}-{index}"
            roots[name] = path
        if roots:
            return roots
    roots = {
        "llmresearch": Path.cwd(),
        "continuity-core": Path("/home/akovanda/game/continuity-core"),
        "continuity-benchmarks": Path("/home/akovanda/game/continuity-benchmarks"),
        "choose-game": Path("/home/akovanda/game/choose-game"),
        "better-mem": Path("/home/akovanda/game/better-mem"),
        "better-mem-platform": Path("/home/akovanda/game/better-mem-platform"),
    }
    return {name: path for name, path in roots.items() if path.exists()}


def build_focus(prompt: str, *, domain: str | None = None, source_signals: list[str] | None = None) -> FocusTuple:
    source_signals = source_signals or []
    lowered = normalize_research_text(prompt)
    for prefix in LEADING_RESEARCH_PHRASES:
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
            break
    lowered = lowered.rstrip(".? ")
    segments = [segment.strip(" ,") for segment in SEGMENT_SPLIT_RE.split(lowered) if segment.strip(" ,")]
    head = segments[0] if segments else lowered
    tail = segments[1:]

    concern: str | None = None
    object_text = head
    if any(head.endswith(pattern) or head == pattern for pattern in GENERIC_HEADS) and tail:
        concern = head
        object_text = tail[0]
        tail = tail[1:]
    elif tail and object_text.split() and object_text.split()[-1] in GENERIC_TAIL_WORDS:
        object_text = head

    signal_repos = extract_signal_repos(source_signals)
    context = ", ".join(signal_repos[:2]) if signal_repos else None
    constraint = None
    leftovers: list[str] = []
    for segment in tail:
        if not constraint and contains_constraint(segment):
            constraint = segment
            continue
        leftovers.append(segment)
    if concern is None and leftovers:
        concern = leftovers[0]
        leftovers = leftovers[1:]
    if context is None and leftovers:
        context = leftovers[-1]

    return FocusTuple(
        domain=domain,
        object=normalize_object_text(clean_phrase(object_text)),
        concern=clean_phrase(concern),
        context=clean_phrase(context),
        constraint=clean_phrase(constraint),
    )


def run_local_research(
    prompt: str,
    *,
    domain: str | None = None,
    source_signals: list[str] | None = None,
    source_roots: list[Path] | None = None,
    max_hits: int = 10,
) -> LocalResearchResult:
    source_signals = source_signals or []
    focus = build_focus(prompt, domain=domain, source_signals=source_signals)
    selected_roots = select_source_roots(source_signals=source_signals, source_roots=source_roots)
    query_terms = build_query_terms(prompt, focus=focus, source_signals=source_signals)
    hits = collect_local_hits(query_terms, focus=focus, roots=selected_roots, max_hits=max_hits)
    claim_drafts = build_claim_drafts(focus, hits)
    guidance = build_guidance(focus, hits=hits, claim_drafts=claim_drafts)
    report_md = render_report(prompt, focus=focus, guidance=guidance) if hits else None
    return LocalResearchResult(
        focus=focus,
        query_terms=query_terms,
        source_roots=[str(root) for root in selected_roots.values()],
        hits=hits,
        claim_drafts=claim_drafts,
        guidance=guidance,
        gaps=guidance.gaps,
        report_md=report_md,
    )


def select_source_roots(*, source_signals: list[str], source_roots: list[Path] | None = None) -> dict[str, Path]:
    if source_roots:
        return {path.name: path for path in source_roots if path.exists()}
    available = default_source_roots()
    signaled = extract_signal_repos(source_signals)
    selected = {name: path for name, path in available.items() if name in signaled}
    return selected or available


def build_query_terms(prompt: str, *, focus: FocusTuple, source_signals: list[str]) -> list[str]:
    repo_labels = set(extract_signal_repos(source_signals))
    candidates: list[str] = []
    for part in [focus.object, focus.concern, focus.constraint]:
        if part and query_term_is_useful(part, repo_labels=repo_labels):
            candidates.append(part)
    for signal in source_signals:
        body = clean_phrase(signal.split(":", 1)[1] if ":" in signal else signal)
        if body and should_keep_exact_signal_phrase(body):
            candidates.append(body)
        if body:
            signal_codeish_terms = extract_codeish_terms(body)
            candidates.extend(signal_codeish_terms)
    candidates.extend(extract_codeish_terms(prompt))
    for part in [focus.object, focus.concern, focus.constraint]:
        if part:
            candidates.extend(extract_keyword_phrases(part))
    deduped: list[str] = []
    for term in candidates:
        cleaned = clean_phrase(term)
        if not cleaned:
            continue
        if cleaned in deduped:
            continue
        if not query_term_is_useful(cleaned, repo_labels=repo_labels):
            continue
        deduped.append(cleaned)
    deduped.sort(key=lambda item: (term_specificity(item), len(item)), reverse=True)
    return deduped[:8]


def collect_local_hits(query_terms: list[str], *, focus: FocusTuple, roots: dict[str, Path], max_hits: int) -> list[LocalEvidenceHit]:
    matches: dict[tuple[str, int], dict] = {}
    file_cache: dict[str, list[str]] = {}
    for term in query_terms:
        for repo_name, root in roots.items():
            for match in search_term(term, root):
                key = (match["path"], match["line"])
                record = matches.setdefault(
                    key,
                    {
                        "path": match["path"],
                        "line": match["line"],
                        "repo_name": repo_name,
                        "matched_terms": [],
                    },
                )
                if term not in record["matched_terms"]:
                    record["matched_terms"].append(term)
    scored: list[LocalEvidenceHit] = []
    for item in matches.values():
        path = item["path"]
        lines = file_cache.setdefault(path, read_text_lines(path))
        quote_text, start_line, end_line = extract_context(lines, item["line"])
        source_type = infer_source_type(path)
        source = SourceCreate(
            locator=path,
            title=short_source_title(path, item["repo_name"]),
            source_type=source_type,
            snippet=quote_text.splitlines()[0][:240],
            snapshot_present=True,
        )
        selector = SourceSelector(
            exact=lines[item["line"] - 1].strip() if 0 < item["line"] <= len(lines) else quote_text,
            deep_link=f"{path}#L{start_line}",
            start_line=start_line,
            end_line=end_line,
        )
        score = score_hit(item["matched_terms"], path=path, source_type=source_type, focus=focus)
        scored.append(
            LocalEvidenceHit(
                source=source,
                selector=selector,
                quote_text=quote_text,
                note=f"Matched {', '.join(item['matched_terms'][:4])} in {short_source_title(path, item['repo_name'])}.",
                matched_terms=item["matched_terms"],
                score=score,
                repo_name=item["repo_name"],
                file_path=path,
            )
        )
    scored.sort(key=lambda hit: (hit.score, len(hit.matched_terms), hit.file_path), reverse=True)
    per_file_counts: defaultdict[str, int] = defaultdict(int)
    selected: list[LocalEvidenceHit] = []
    for hit in scored:
        if per_file_counts[hit.file_path] >= 2:
            continue
        per_file_counts[hit.file_path] += 1
        selected.append(hit)
        if len(selected) >= max_hits:
            break
    return selected


def search_term(term: str, root: Path) -> list[dict[str, str | int]]:
    try:
        return run_rg(term, root)
    except FileNotFoundError:
        return run_python_scan(term, root)


def build_claim_drafts(focus: FocusTuple, hits: list[LocalEvidenceHit]) -> list[LocalClaimDraft]:
    if not hits:
        return []
    object_text = focus.object or focus.label or "the topic"
    path_summary = summarize_paths(hits[:3])
    top_terms = summarize_terms(hits[:5])
    drafts: list[LocalClaimDraft] = []
    primary_indexes = list(range(min(3, len(hits))))
    primary_confidence = confidence_for_indexes(hits, primary_indexes)
    primary_status: ClaimStatus = "supported" if len({hits[index].file_path for index in primary_indexes}) >= 2 else "partial"
    drafts.append(
        LocalClaimDraft(
            title=f"{object_text.title()} is already explicit in local evidence",
            statement=(
                f"Matched terms such as {top_terms} in {path_summary} show that {object_text} is already an explicit "
                f"implementation surface in {focus.context or 'the current project stack'}."
            ),
            excerpt_indexes=primary_indexes,
            status=primary_status,
            confidence=primary_confidence,
        )
    )
    validation_indexes = [index for index, hit in enumerate(hits) if hit.source.source_type in {"test", "script", "documentation", "report"}]
    validation_indexes = validation_indexes[:3]
    if validation_indexes:
        drafts.append(
            LocalClaimDraft(
                title=f"{object_text.title()} has a concrete validation surface",
                statement=(
                    f"Tests, scripts, or docs in {summarize_paths([hits[index] for index in validation_indexes])} tie {object_text} "
                    f"to concrete verification or operational workflows instead of leaving it as a purely conceptual concern."
                ),
                excerpt_indexes=validation_indexes,
                status="supported" if len(validation_indexes) >= 2 else "partial",
                confidence=confidence_for_indexes(hits, validation_indexes),
            )
        )
    constraint_indexes = [
        index
        for index, hit in enumerate(hits)
        if focus.constraint and any(part in hit.quote_text.lower() for part in focus.constraint.lower().split())
    ][:3]
    if constraint_indexes:
        drafts.append(
            LocalClaimDraft(
                title=f"{object_text.title()} is bounded by an explicit operating constraint",
                statement=(
                    f"Evidence in {summarize_paths([hits[index] for index in constraint_indexes])} ties {object_text} to "
                    f"{focus.constraint}, which narrows the real design space for the next implementation pass."
                ),
                excerpt_indexes=constraint_indexes,
                status="supported",
                confidence=confidence_for_indexes(hits, constraint_indexes),
            )
        )
    return drafts


def build_gaps(focus: FocusTuple, hits: list[LocalEvidenceHit]) -> list[str]:
    gaps: list[str] = []
    if not hits:
        return [f"No live local evidence was found for {focus.label or 'this topic'} in the selected source roots."]
    repo_names = sorted({hit.repo_name for hit in hits})
    if len(repo_names) == 1:
        gaps.append(f"Evidence is concentrated in {repo_names[0]}, so cross-repo support is still thin.")
    if not any(hit.source.source_type == "test" for hit in hits):
        gaps.append("Direct test evidence was thin, so the current support is weighted toward code, docs, or scripts.")
    if focus.constraint and not any(focus.constraint.lower() in hit.quote_text.lower() for hit in hits):
        gaps.append(f"The constraint `{focus.constraint}` was not matched directly, so that part of the question still needs deeper evidence.")
    return gaps


def build_guidance(focus: FocusTuple, *, hits: list[LocalEvidenceHit], claim_drafts: list[LocalClaimDraft]) -> LocalGuidanceDraft:
    gaps = build_gaps(focus, hits)
    evidence_now = build_evidence_points(hits)
    needs = build_needs(focus, hits)
    wants = build_wants(focus, hits)
    follow_ups = build_follow_ups(focus, hits=hits, gaps=gaps)
    current_guidance = [claim.statement for claim in claim_drafts[:3]]
    if hits and len({hit.repo_name for hit in hits}) == 1:
        current_guidance.append(
            f"Treat the current support for {focus.object or focus.label or 'this topic'} as directional until a second repo or document family corroborates it."
        )
    if hits and not any(hit.source.source_type == "test" for hit in hits):
        current_guidance.append(
            f"Do not freeze the design around {focus.object or focus.label or 'this topic'} yet; the evidence base still needs direct verification coverage."
        )
    deduped_guidance: list[str] = []
    for item in current_guidance:
        if item not in deduped_guidance:
            deduped_guidance.append(item)
    return LocalGuidanceDraft(
        current_guidance=deduped_guidance,
        evidence_now=evidence_now,
        gaps=gaps,
        needs=needs,
        wants=wants,
        follow_ups=follow_ups,
    )


def render_report(prompt: str, *, focus: FocusTuple, guidance: LocalGuidanceDraft) -> str:
    lines = [
        f"# {prompt}",
        "",
        "## Current Guidance",
    ]
    if guidance.current_guidance:
        lines.extend(f"- {point}" for point in guidance.current_guidance)
    else:
        lines.append("- No source-backed guidance was synthesized from this pass.")
    lines.extend(["", "## What Evidence Supports Right Now"])
    if guidance.evidence_now:
        lines.extend(f"- {point}" for point in guidance.evidence_now)
    else:
        lines.append("- No strong evidence bullets were captured.")
    lines.extend(["", "## Gaps"])
    if guidance.gaps:
        lines.extend(f"- {gap}" for gap in guidance.gaps)
    else:
        lines.append("- No major evidence gaps were detected in the selected local source roots.")
    lines.extend(["", "## Needs"])
    if guidance.needs:
        lines.extend(f"- {need}" for need in guidance.needs)
    else:
        lines.append("- No immediate must-have follow-up work was identified.")
    lines.extend(["", "## Wants"])
    if guidance.wants:
        lines.extend(f"- {want}" for want in guidance.wants)
    else:
        lines.append("- No lower-priority expansion work was identified.")
    lines.extend(["", "## Follow-up Questions"])
    if guidance.follow_ups:
        for index, follow_up in enumerate(guidance.follow_ups, start=1):
            lines.append(f"{index}. [{follow_up.reason} | {follow_up.priority_score:.2f}] {follow_up.prompt}")
    else:
        lines.append("1. No follow-up questions were generated from this pass.")
    lines.extend(["", "## Registry State", f"- Focus label: {focus.label}", f"- Domain: {focus.domain or 'general'}"])
    if focus.context:
        lines.append(f"- Context: {focus.context}")
    if focus.constraint:
        lines.append(f"- Constraint: {focus.constraint}")
    return "\n".join(lines).rstrip() + "\n"


def build_evidence_points(hits: list[LocalEvidenceHit]) -> list[str]:
    points: list[str] = []
    for hit in hits[:5]:
        matched_terms = ", ".join(hit.matched_terms[:3]) or "matched evidence"
        line = hit.selector.start_line or 1
        points.append(f"{short_source_title(hit.file_path, hit.repo_name)}:{line} matched {matched_terms}.")
    return points


def build_needs(focus: FocusTuple, hits: list[LocalEvidenceHit]) -> list[str]:
    if not hits:
        return [f"Need direct local evidence for {focus.label or 'this topic'} before storing reusable claims."]
    repo_names = sorted({hit.repo_name for hit in hits})
    source_types = {hit.source.source_type for hit in hits}
    needs: list[str] = []
    if len(repo_names) == 1:
        needs.append(
            f"Need corroborating evidence for {focus.object or focus.label or 'this topic'} outside {repo_names[0]} before treating it as cross-stack guidance."
        )
    if "test" not in source_types and "report" not in source_types:
        needs.append(
            f"Need direct test or benchmark evidence for {focus.object or focus.label or 'this topic'} so future reuse is not carried only by implementation files."
        )
    if focus.constraint and not any(focus.constraint.lower() in hit.quote_text.lower() for hit in hits):
        needs.append(f"Need explicit evidence that addresses the `{focus.constraint}` constraint.")
    return needs


def build_wants(focus: FocusTuple, hits: list[LocalEvidenceHit]) -> list[str]:
    if not hits:
        return [f"Want adjacent terminology, docs, or benchmarks that might expose alternative names for {focus.label or 'this topic'}."]
    source_types = {hit.source.source_type for hit in hits}
    wants: list[str] = []
    if len(hits) < 5:
        wants.append(f"Want a broader evidence spread for {focus.object or focus.label or 'this topic'} so synthesis depends on more than a narrow file slice.")
    if "documentation" not in source_types:
        wants.append(f"Want documentation or design-note evidence that explains why {focus.object or focus.label or 'this topic'} exists.")
    if "report" not in source_types:
        wants.append(f"Want benchmark or artifact evidence showing how {focus.object or focus.label or 'this topic'} affects measured outcomes.")
    return wants


def build_follow_ups(focus: FocusTuple, *, hits: list[LocalEvidenceHit], gaps: list[str]) -> list[LocalFollowUpDraft]:
    object_text = focus.object or focus.label or "this topic"
    repo_names = sorted({hit.repo_name for hit in hits})
    prompts: list[LocalFollowUpDraft] = []
    if len(repo_names) == 1:
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research corroborating evidence for {object_text} across repos beyond {repo_names[0]}.",
                reason="need",
                rationale="Current support is concentrated in a single repo.",
                priority_score=0.95,
            )
        )
    if not any(hit.source.source_type in {"test", "report"} for hit in hits):
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research test or benchmark evidence for {object_text}.",
                reason="need",
                rationale="The current pass found little or no direct verification evidence.",
                priority_score=0.92,
            )
        )
    if focus.constraint and not any(focus.constraint.lower() in hit.quote_text.lower() for hit in hits):
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research direct evidence for {object_text} under {focus.constraint}.",
                reason="gap",
                rationale="The named constraint was not matched directly in the evidence.",
                priority_score=0.9,
            )
        )
    if not any(hit.source.source_type == "documentation" for hit in hits):
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research documentation and design notes that explain {object_text}.",
                reason="want",
                rationale="Operational or architectural context is still thin.",
                priority_score=0.72,
            )
        )
    if not any(hit.source.source_type == "report" for hit in hits):
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research benchmark or artifact evidence that measures the impact of {object_text}.",
                reason="want",
                rationale="The current evidence does not yet show measured outcomes.",
                priority_score=0.68,
            )
        )
    if not prompts and gaps:
        prompts.append(
            LocalFollowUpDraft(
                prompt=f"Research deeper local evidence for {object_text} using adjacent terms and related implementation surfaces.",
                reason="gap",
                rationale=gaps[0],
                priority_score=0.75,
            )
        )
    deduped: list[LocalFollowUpDraft] = []
    seen_prompts: set[str] = set()
    for item in prompts:
        normalized = normalize_research_text(item.prompt)
        if normalized in seen_prompts:
            continue
        seen_prompts.add(normalized)
        deduped.append(item)
    return deduped[:5]


def run_rg(term: str, root: Path) -> list[dict[str, str | int]]:
    if not term or not root.exists():
        return []
    command = ["rg", "--json", "--line-buffered", "-n", "-i", "-F", "-m", "1", "--max-filesize", "1M"]
    for glob in RG_GLOBS:
        command.extend(["-g", glob])
    command.extend([term, str(root)])
    results: list[dict[str, str | int]] = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "match":
                continue
            data = payload["data"]
            path = data["path"]["text"]
            results.append(
                {
                    "path": path,
                    "line": data["line_number"],
                }
            )
            if len(results) >= TERM_MATCH_LIMIT:
                process.terminate()
                break
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
    finally:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    return results


def run_python_scan(term: str, root: Path) -> list[dict[str, str | int]]:
    if not term or not root.exists():
        return []
    needle = term.casefold()
    results: list[dict[str, str | int]] = []
    for path in iter_searchable_files(root):
        for index, line in enumerate(read_text_lines(str(path)), start=1):
            if needle in line.casefold():
                results.append({"path": str(path), "line": index})
                if len(results) >= TERM_MATCH_LIMIT:
                    return results
    return results


def iter_searchable_files(root: Path):
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIR_NAMES]
        for filename in filenames:
            path = Path(current_root) / filename
            if should_skip_file(path):
                continue
            if not path.is_file():
                continue
            yield path


def should_skip_file(path: Path) -> bool:
    lowered = path.name.lower()
    if any(lowered.endswith(suffix) for suffix in IGNORED_FILE_SUFFIXES):
        return True
    try:
        return path.stat().st_size > MAX_SEARCH_FILE_SIZE_BYTES
    except OSError:
        return True


def read_text_lines(path: str) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="latin-1").splitlines()
    except OSError:
        return []


def extract_context(lines: list[str], line_number: int, radius: int = 1) -> tuple[str, int, int]:
    if not lines:
        return "", line_number, line_number
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    snippet = "\n".join(lines[start - 1 : end]).strip()
    return snippet, start, end


def infer_source_type(path: str) -> str:
    normalized = path.lower()
    if "/tests/" in normalized or normalized.endswith("_test.py") or normalized.endswith("test.py"):
        return "test"
    if "/scripts/" in normalized or normalized.endswith(".sh"):
        return "script"
    if "/docs/" in normalized or normalized.endswith("readme.md"):
        return "documentation"
    if "/benchmarks/" in normalized or "/artifacts/" in normalized or normalized.endswith(".json"):
        return "report"
    if normalized.endswith(".py") or normalized.endswith(".ts") or normalized.endswith(".tsx"):
        return "code"
    return "local_file"


def short_source_title(path: str, repo_name: str) -> str:
    source_path = Path(path)
    parts = source_path.parts
    if repo_name in parts:
        index = parts.index(repo_name)
        return f"{repo_name}/{'/'.join(parts[index + 1:])}"
    return f"{repo_name}/{source_path.name}"


def score_hit(matched_terms: list[str], *, path: str, source_type: str, focus: FocusTuple) -> float:
    normalized_path = path.lower()
    score = float(len(matched_terms) * 2)
    if focus.object and focus.object.lower() in normalized_path:
        score += 2.5
    if source_type == "test":
        score += 2.0
    elif source_type in {"script", "documentation", "report"}:
        score += 1.5
    if any(token in normalized_path for token in ("readme", "methodology", "release", "history")):
        score += 1.0
    return round(score, 3)


def summarize_paths(hits: list[LocalEvidenceHit]) -> str:
    labels = []
    for hit in hits:
        label = short_source_title(hit.file_path, hit.repo_name)
        if label not in labels:
            labels.append(label)
    return ", ".join(labels[:3])


def summarize_terms(hits: list[LocalEvidenceHit]) -> str:
    counter = Counter(term for hit in hits for term in hit.matched_terms)
    return ", ".join(term for term, _ in counter.most_common(4))


def confidence_for_indexes(hits: list[LocalEvidenceHit], indexes: list[int]) -> float:
    if not indexes:
        return 0.0
    unique_files = len({hits[index].file_path for index in indexes})
    unique_repos = len({hits[index].repo_name for index in indexes})
    return round(min(0.92, 0.55 + (0.09 * unique_files) + (0.05 * unique_repos)), 2)


def normalize_research_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def clean_phrase(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" ,.-")
    return cleaned or None


def normalize_object_text(value: str | None) -> str | None:
    if not value:
        return None
    clipped = RELATIVE_CLAUSE_SPLIT_RE.split(value, maxsplit=1)[0].strip(" ,.-")
    words = clipped.split()
    while words and words[0] in {"the", "current", "public"}:
        words = words[1:]
    while len(words) > 2 and words[-1] in GENERIC_TAIL_WORDS:
        words = words[:-1]
    return " ".join(words)


def contains_constraint(segment: str) -> bool:
    normalized = segment.lower()
    return any(term in normalized for term in CONSTRAINT_TERMS)


def extract_signal_repos(source_signals: list[str]) -> list[str]:
    repos: list[str] = []
    for signal in source_signals:
        if ":" not in signal:
            continue
        repo = signal.split(":", 1)[0].strip()
        if repo and repo not in repos:
            repos.append(repo)
    return repos


def extract_codeish_terms(text: str) -> list[str]:
    terms = re.findall(r"[a-z0-9_./-]{4,}", text.lower())
    return [term for term in terms if term not in STOPWORDS]


def extract_keyword_phrases(text: str) -> list[str]:
    lowered = normalize_research_text(text)
    words = [word for word in re.findall(r"[a-z0-9_-]+", lowered) if word not in STOPWORDS]
    phrases: list[str] = []
    for size in (3, 2):
        for index in range(len(words) - size + 1):
            phrase = " ".join(words[index : index + size])
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases[:8]


def term_specificity(term: str) -> tuple[int, int]:
    parts = term.split()
    codeish = int(bool(re.search(r"[_./-]", term)))
    return (codeish, len(parts))


def should_keep_exact_signal_phrase(term: str) -> bool:
    tokens = query_tokens(term)
    if not 2 <= len(tokens) <= 7:
        return False
    informative = informative_query_tokens(tokens)
    if len(informative) < 2:
        return False
    return has_query_anchor(term) or len(tokens) <= 4


def query_term_is_useful(term: str, *, repo_labels: set[str]) -> bool:
    normalized_term = normalize_research_text(term)
    if not normalized_term or normalized_term in repo_labels:
        return False
    tokens = query_tokens(term)
    if not tokens or len(tokens) > 8:
        return False
    if len(tokens) == 1:
        token = tokens[0]
        if token in repo_labels or token in GENERIC_QUERY_WORDS:
            return False
        return has_query_anchor(term) or len(token) >= 10
    informative = informative_query_tokens(tokens)
    if len(informative) < 2:
        return False
    if len(tokens) <= 2 and not has_query_anchor(term):
        return False
    return True


def query_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_-]+", normalize_research_text(text)) if token]


def informative_query_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in STOPWORDS and token not in GENERIC_QUERY_WORDS]


def has_query_anchor(term: str) -> bool:
    return bool(re.search(r"[_./-]|\d", term))
