---
name: research-memory-retrieval
description: Research LLM memory and retrieval topics with a source-backed workflow. Use when Codex needs to investigate agent memory, long-term or cross-session memory, RAG retrieval, recall/precision tradeoffs, reranking, indexing, freshness, provenance, context management, or retrieval failures, and the result should be searched, reused, and optionally deposited into a Research Registry as annotations, findings, and reports.
---

# Research Memory Retrieval

## Overview

Use this skill to research memory and retrieval topics against a Research Registry backend. Search existing registry content first, reuse mature artifacts when they already answer the question, add new source-anchored annotations only when the registry is missing evidence, then synthesize findings and optionally compile a report.

## Backend Check

- Confirm that the Research Registry MCP tools are available before doing any work.
- Expect these tools at minimum: `search`, `create_run`, `get_source`, `get_annotation`, `get_finding`, `get_report`, `add_annotation`, `create_finding`, `compile_report`, and `publish`.
- If those tools are unavailable, stop and say the skill requires a configured Research Registry MCP server. Do not invent stored memory and do not silently fall back to unsupported ad hoc notes.

## Workflow

1. Search first.
   Search the registry for the exact topic, close synonyms, likely source titles, and failure-mode phrases before adding anything new.
2. Decide whether the question is already answered.
   Reuse mature findings or reports when the registry already has enough evidence. Prefer retrieval over new deposition.
3. Create a run only when genuine gaps remain.
   Use `create_run` to group new research artifacts and preserve provenance.
4. Deposit annotations before any synthesis.
   Add source-anchored annotations with deep links, exact quotes when available, accurate subjects, and tags tied to the retrieval problem.
5. Create findings only after evidence exists.
   Build a finding from one or more annotations. Prefer at least two annotations when the claim is non-trivial.
6. Compile a report only when the topic is mature.
   Use a report when the user wants a higher-level synthesis or when multiple findings need to be combined. Skip the report for exploratory or under-evidenced work.
7. Publish only when explicitly asked.
   Human review and publication are separate workflows. Default to private artifacts.

## Decision Rules

- Stop after retrieval when an existing finding or report already addresses the user’s question with sufficient provenance.
- Create new annotations when the registry lacks source-backed evidence for the topic or when existing artifacts are stale, too shallow, or clearly off-topic.
- Refuse to create unsupported artifacts when no anchored sources are available.
- Prefer narrower, falsifiable findings over broad claims.
- Treat memory and retrieval as separate but linked concerns: storage quality, retrieval quality, freshness, provenance, and context assembly all matter.

## Topic Coverage

- Use [references/topic-taxonomy.md](references/topic-taxonomy.md) to expand queries and keep scope inside LLM memory and retrieval.
- Use [references/workflow.md](references/workflow.md) for the exact MCP-first sequence.
- Use [references/deposit-rubric.md](references/deposit-rubric.md) for quality thresholds and stop conditions.

## Output Expectations

- For retrieval-only questions, return the relevant existing artifacts and explain why they are sufficient.
- For gap-filling research, produce new annotations and at least one finding tied to the run.
- For mature topics, compile a report that still points back to the underlying findings, annotations, and sources.
- Surface uncertainty explicitly when sources disagree, freshness is questionable, or the available corpus is thin.

## Examples

- "Research whether reranking should be separate from retrieval in RAG systems, and reuse anything already in memory."
- "Investigate cross-session agent memory failures caused by stale indexes."
- "Look up what metrics matter for evaluating long-term memory retrieval."
- "Find existing research on provenance-backed memory retrieval before you add anything new."

## Notes

- Keep the skill generic. Do not hard-code repo-local paths into the reasoning.
- Treat this as a research workflow skill, not a publish/review skill.
- Prefer MCP over HTTP. Use the website only for inspection when needed.
