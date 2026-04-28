---
name: research-capture
description: Implicitly capture research-oriented work into a Research Registry. Use when Codex is asked to research, investigate, compare, gather sources, survey prior work, review a change, triage a failing repo workflow, or answer repo-specific command questions, and the result should be searched first, stored privately by default, summarized explicitly, and queued for retry if the registry path is temporarily unavailable.
---

# Research Capture

## Overview

Use this skill as the default workflow for research intent and repo-aware triage intent. Search existing registry content first, perform source-backed research when needed, store `Question`, `ResearchSession`, `Excerpt`, `Claim`, and `Report` artifacts privately by default, and tell the user exactly what was reused, stored, or queued.

## Backend Check

- Prefer the Research Registry MCP tools when they are available.
- Expect these tools at minimum: `search`, `backend_status`, `create_question`, `create_session`, `get_source`, `get_excerpt`, `get_claim`, `get_report`, `create_source`, `add_excerpt`, `create_claim`, and `create_report`.
- Assume backend selection precedence is: explicit override, named profile, org profile, hosted default, then localhost default.
- Flush pending queue items first when `research-registry-capture-queue` is available.

## Delegation

- Delegate memory and retrieval topics to `$research-memory-retrieval`.
- Keep repo-aware command routing, AGENTS resolution, preflight, review, and triage inside this skill.
- Keep ownership of storage behavior and the explicit capture summary even when domain work is delegated.
- Preserve the guidance-first summary shape for general research, and use the repo-aware triage shape for command/review/debug passes.

## Workflow

1. Detect research intent or repo-aware triage intent.
2. Flush pending queue items first.
3. For repo-aware prompts, inspect `.codex/repo-profile.toml`, the nearest applicable `AGENTS.md` files, local manifests/configs, targeted `rg` hits, git state, and coverage artifacts before widening the search surface.
4. Search before adding anything new.
5. Reuse fresh guidance when it already covers the question.
6. Create a research session only when real gaps remain.
7. Add source-backed excerpts before creating claims.
8. Create claims only after evidence exists.
9. Always create a guidance report for a new implicit research session.
10. Summarize what was reused, what was stored, what follow-up questions were created, and what was queued.

## Queue Fallback

- If the registry path is unavailable, continue the research instead of discarding it.
- Queue the capture bundle locally with `research-registry-capture-queue enqueue`.
- Never silently drop source-backed research because the backend was temporarily unavailable.

## Notes

- Default all new artifacts to private.
- Do not publish implicitly.
- Treat localhost as the default backend unless the user or environment points at a shared server.
- Prefer MCP when available, but preserve queue fallback behavior when storage is temporarily unavailable.
- When a checked-in repo profile or path-specific `AGENTS.md` files are present, capture repo-aware triage and review work implicitly rather than treating it as generic web research.
