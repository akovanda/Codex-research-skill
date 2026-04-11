from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import subprocess

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


class LocalResearchResult(BaseModel):
    focus: FocusTuple
    query_terms: list[str]
    source_roots: list[str]
    hits: list[LocalEvidenceHit]
    claim_drafts: list[LocalClaimDraft]
    gaps: list[str] = Field(default_factory=list)
    report_md: str | None = None


def default_source_roots() -> dict[str, Path]:
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
    gaps = build_gaps(focus, hits)
    report_md = render_report(prompt, focus=focus, hits=hits, claim_drafts=claim_drafts, gaps=gaps) if hits else None
    return LocalResearchResult(
        focus=focus,
        query_terms=query_terms,
        source_roots=[str(root) for root in selected_roots.values()],
        hits=hits,
        claim_drafts=claim_drafts,
        gaps=gaps,
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
    candidates: list[str] = []
    for part in [focus.object, focus.concern, focus.constraint]:
        if part and part not in candidates:
            candidates.append(part)
    for signal in source_signals:
        if ":" in signal:
            candidates.append(clean_phrase(signal.split(":", 1)[1]))
        candidates.extend(extract_codeish_terms(signal))
    candidates.extend(extract_codeish_terms(prompt))
    candidates.extend(extract_keyword_phrases(prompt))
    deduped: list[str] = []
    for term in candidates:
        cleaned = clean_phrase(term)
        if not cleaned:
            continue
        if cleaned in deduped:
            continue
        if len(cleaned) < 4:
            continue
        deduped.append(cleaned)
    deduped.sort(key=lambda item: (term_specificity(item), len(item)), reverse=True)
    return deduped[:8]


def collect_local_hits(query_terms: list[str], *, focus: FocusTuple, roots: dict[str, Path], max_hits: int) -> list[LocalEvidenceHit]:
    matches: dict[tuple[str, int], dict] = {}
    file_cache: dict[str, list[str]] = {}
    for term in query_terms:
        for repo_name, root in roots.items():
            for match in run_rg(term, root):
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


def render_report(prompt: str, *, focus: FocusTuple, hits: list[LocalEvidenceHit], claim_drafts: list[LocalClaimDraft], gaps: list[str]) -> str:
    repo_names = ", ".join(sorted({hit.repo_name for hit in hits}))
    direct_answer = (
        f"Based on live local evidence from {repo_names}, {focus.object or focus.label or 'the topic'} is already represented in real files, "
        f"with the strongest signals coming from {summarize_paths(hits[:3])}."
    )
    lines = [
        f"# {prompt}",
        "",
        "## Direct Answer",
        direct_answer,
        "",
        "## Focus",
        f"- Label: {focus.label}",
        f"- Domain: {focus.domain or 'general'}",
    ]
    if focus.context:
        lines.append(f"- Context: {focus.context}")
    if focus.constraint:
        lines.append(f"- Constraint: {focus.constraint}")
    lines.extend(["", "## Claims"])
    for index, claim in enumerate(claim_drafts, start=1):
        lines.append(f"{index}. [{claim.status} | {claim.confidence:.2f}] {claim.statement}")
    lines.extend(["", "## Evidence"])
    for claim in claim_drafts:
        lines.append(f"### {claim.title}")
        for excerpt_index in claim.excerpt_indexes[:3]:
            hit = hits[excerpt_index]
            lines.append(f"- {short_source_title(hit.file_path, hit.repo_name)}:{hit.selector.start_line}")
            lines.append(f"  {hit.quote_text.strip()}")
    lines.extend(["", "## Gaps"])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append("- No major evidence gaps were detected in the selected local source roots.")
    return "\n".join(lines).rstrip() + "\n"


def run_rg(term: str, root: Path) -> list[dict[str, str | int]]:
    if not term or not root.exists():
        return []
    command = ["rg", "--json", "-n", "-i", "-F"]
    for glob in RG_GLOBS:
        command.extend(["-g", glob])
    command.extend([term, str(root)])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    results: list[dict[str, str | int]] = []
    for line in completed.stdout.splitlines():
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
    return results


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
    words = value.split()
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
