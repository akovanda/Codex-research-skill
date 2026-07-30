---
name: research-memory-retrieval
description: Deprecated compatibility workflow for the former memory and retrieval specialist. Invoke only when the user explicitly requests the legacy adapter and RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 is enabled. New work should use research-recall and research-deposit.
---

# Research Memory Retrieval

> Deprecated legacy adapter. It is not implicitly invokable and requires
> `RESEARCH_REGISTRY_LEGACY_HEURISTICS=1`.

## Overview

Use this skill only to preserve an existing specialist workflow during
migration. The default v2 path uses the focused `research-recall` and
`research-deposit` skills.

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
