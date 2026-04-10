---
name: research-capture
description: Implicitly capture research-oriented work into a Research Registry. Use when Codex is asked to research, investigate, compare, gather sources, survey prior work, or otherwise perform source-backed research on any topic, and the result should be searched first, stored privately by default, summarized explicitly, and queued for retry if the registry path is temporarily unavailable.
---

# Research Capture

## Overview

Use this skill as the default workflow for research intent. Search existing registry content first, perform source-backed research when needed, store runs, annotations, findings, and a report privately by default, and tell the user exactly what was reused, stored, or queued.

## Trigger Rule

- Treat requests like `research X`, `investigate Y`, `look into Z`, `compare A vs B`, `gather sources`, and similar phrasing as research requests.
- Do not use this skill for normal coding, edits, bug fixes, or casual factual questions that are not framed as research.

## Backend Check

- Prefer the Research Registry MCP tools when they are available.
- Expect these tools at minimum: `search`, `backend_status`, `create_run`, `get_source`, `get_annotation`, `get_finding`, `get_report`, `add_annotation`, `create_finding`, and either `create_report` or `compile_report`.
- Assume backend selection precedence is: explicit override, named profile, org profile, hosted default, then localhost default.
- Before new research, try to flush pending capture bundles with `research-registry-capture-queue flush` if that command exists.

## Delegation

- If the topic is about memory, long-term memory, RAG, retrieval, reranking, indexing, provenance, freshness, or context management, delegate the domain reasoning workflow to `$research-memory-retrieval`.
- Keep ownership of storage behavior and the explicit user summary even when domain work is delegated.

## Workflow

1. Detect research intent.
   If the request is research-shaped, use this workflow automatically.
2. Flush pending queue items first.
   If the queue CLI is available, flush queued bundles before starting new work.
3. Search before adding.
   Search the registry for exact terms, nearby synonyms, and likely source titles.
4. Reuse when sufficient.
   If existing findings or reports already answer the question with acceptable provenance, reuse them and still provide an explicit storage/reuse summary.
5. Create a run for new work.
   When new research is needed, create a run and preserve provenance for every new artifact.
6. Add source-backed annotations.
   Deposit annotations before any synthesis. Do not store unsupported claims.
7. Create findings.
   Build findings from the annotations.
8. Always create a report.
   Store a private report for every implicit research session. Prefer `create_report` when you already have a real synthesis; use `compile_report` only when a skeletal summary is acceptable.
9. Summarize explicitly to the user.
   State what backend and namespace were used, what was reused, what was stored, what report was created, and what was queued.

## Queue Fallback

- If the registry path is unavailable, continue the research instead of discarding it.
- Queue the capture bundle locally with `research-registry-capture-queue enqueue` and retry on the next research request.
- Never silently drop source-backed research because the backend was temporarily unavailable.

## References

- Use [references/workflow.md](references/workflow.md) for the exact research-capture sequence.
- Use [references/routing.md](references/routing.md) for delegation rules and topic routing.
- Use [references/queue-fallback.md](references/queue-fallback.md) for queue semantics and bundle structure.

## Output Expectations

- Always include an explicit capture summary.
- Default all new artifacts to private.
- Always end stored research sessions with a report id or a queue id.
- Call out failures to store or replay queued captures.

## Examples

- "Research the tradeoffs between SQLite and Postgres for analytics workloads."
- "Compare model distillation approaches and gather sources."
- "Investigate long-term memory structure for LLM agents."
- "Look into failure modes of retrieval-augmented generation."

## Notes

- Keep the storage flow generic across topics.
- Do not publish implicitly.
- Treat public namespace publishing and shared global-index inclusion as separate states.
- Prefer MCP when available, but use the queue fallback when storage is temporarily unavailable.
