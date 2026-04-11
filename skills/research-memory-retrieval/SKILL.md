---
name: research-memory-retrieval
description: Research LLM memory and retrieval topics with a source-backed workflow. Use when Codex needs to investigate agent memory, long-term or cross-session memory, RAG retrieval, recall/precision tradeoffs, reranking, indexing, freshness, provenance, context management, or retrieval failures, and the result should be searched, reused, and optionally deposited into a Research Registry as excerpts, claims, and guidance reports.
---

# Research Memory Retrieval

## Overview

Use this skill to research memory and retrieval topics against a Research Registry backend. Search existing registry content first, reuse mature guidance when it already answers the question, add new source-backed excerpts only when the registry is missing evidence, then synthesize claims and a guidance report when the topic is mature enough.

This skill may be invoked directly or delegated to by `$research-capture`.

## Backend Check

- Confirm that the Research Registry MCP tools are available before doing any work.
- Expect these tools at minimum: `search`, `create_question`, `create_session`, `get_source`, `get_excerpt`, `get_claim`, `get_report`, `create_source`, `add_excerpt`, `create_claim`, `create_report`, and `publish`.
- If those tools are unavailable, stop and say the skill requires a configured Research Registry MCP server.

## Workflow

1. Search existing registry content first.
2. Reuse fresh guidance or claims when they already cover the question.
3. Create a session only when real evidence gaps remain.
4. Deposit source-backed excerpts before any synthesis.
5. Build claims from excerpts only after the evidence exists.
6. Create a guidance report when the topic is mature enough to carry forward.
7. Publish only when explicitly asked.
8. Return a guidance-first summary that preserves evidence and follow-up questions.

## Decision Rules

- Search existing registry content first.
- Refuse to create unsupported artifacts when no anchored sources are available.
- Publish only when explicitly asked.
- Prefer narrower, falsifiable claims over broad recaps.
- Treat memory and retrieval as linked concerns: freshness, provenance, retrieval quality, reranking, and context assembly all matter.

## Output Expectations

- Reuse mature reports or claims before creating new storage.
- For new work, create excerpts, at least one claim, and a guidance report when the evidence is strong enough.
- Surface uncertainty explicitly when sources disagree or freshness is questionable.
- Use the guidance-first summary shape from [references/summary-contract.md](references/summary-contract.md).
