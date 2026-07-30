---
name: research-capture
description: Deprecated compatibility workflow for the former broad research, repository-triage, and review capture behavior. Invoke only when the user explicitly requests the legacy adapter and RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 is enabled. New work should use research-recall and research-deposit.
---

# Research Capture

> Deprecated legacy adapter. It is not implicitly invokable and requires
> `RESEARCH_REGISTRY_LEGACY_HEURISTICS=1`.

## Overview

Use this skill only to preserve an existing legacy workflow during migration.
The default v2 path uses the focused `research-recall` and
`research-deposit` skills.

## Backend Check

- Prefer the Research Registry MCP tools when they are available.
- Expect these tools at minimum: `search`, `backend_status`, `create_question`, `create_session`, `get_source`, `get_excerpt`, `get_claim`, `get_report`, `create_source`, `add_excerpt`, `create_claim`, and `create_report`.
- Prefer `create_research_bundle` when it is available for a complete new research pass; it reduces schema drift by creating the question, session, sources, excerpts, claims, and report together.
- Assume backend selection precedence is: explicit override, named profile, org profile, hosted default, then localhost default.
- Flush pending queue items first when `research-registry-capture-queue` is available.

## Delegation

- Delegate memory and retrieval topics to `$research-memory-retrieval`.
- Keep repo-aware command routing, AGENTS resolution, preflight, review, and triage inside this skill.
- Keep ownership of storage behavior and the explicit capture summary even when domain work is delegated.
- Preserve the guidance-first summary shape for general research, and use the repo-aware triage shape for command/review/debug passes.

## Workflow

1. Confirm the user explicitly requested the legacy workflow.
2. Flush pending queue items first.
3. For repo-aware prompts, inspect `.codex/repo-profile.toml`, the nearest applicable `AGENTS.md` files, local manifests/configs, targeted `rg` hits, git state, and coverage artifacts before widening the search surface.
4. Search before adding anything new.
5. Reuse fresh guidance when it already covers the question.
6. Create a research session only when real gaps remain.
7. Add source-backed excerpts before creating claims.
8. Create claims only after evidence exists.
9. Always create a guidance report for a new legacy research session.
10. Summarize what was reused, what was stored, what follow-up questions were created, and what was queued.

## Queue Fallback

- If the registry path is unavailable, continue the research instead of discarding it.
- Queue the capture bundle locally with `research-registry-capture-queue enqueue`.
- Never silently drop source-backed research because the backend was temporarily unavailable.

## Notes

- Default all new artifacts to private.
- Do not publish or invoke this skill implicitly.
- Treat localhost as the default backend unless the user or environment points at a shared server.
- Prefer MCP when available, but preserve queue fallback behavior when storage is temporarily unavailable.
- Do not route repo-aware triage or review work automatically.
