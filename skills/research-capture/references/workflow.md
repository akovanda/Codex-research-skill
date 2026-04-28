# Workflow

Use this sequence for any research-shaped or repo-aware capture request.

## 1. Flush Before New Work

- if `research-registry-capture-queue` is available, run `flush` before new research
- if queued items fail again, mention that explicitly in the final summary

## 2. Search First

- search the registry for exact terms, adjacent vocabulary, and likely source titles
- prefer reuse when an existing report or claim already answers the question with enough provenance

## 3. Route By Topic

- delegate memory/retrieval topics to `$research-memory-retrieval`
- keep repo-aware command-routing, triage, and review prompts inside `research-capture`
- otherwise stay in the general research-capture flow

## 4. Check The Repo First When Applicable

- if the repo has `.codex/repo-profile.toml`, load it first
- resolve the nearest `AGENTS.md` files for the affected path before summarizing instructions
- inspect local manifests, config files, targeted `rg` hits, git state, and coverage artifacts before widening the search surface

## 5. Store New Research

- create a research session for new work
- add source-backed excerpts first
- create claims from those excerpts
- create a private guidance report for the session
- create follow-up questions when gaps, needs, or wants are concrete

## 6. Summarize Explicitly

- tell the user what was reused
- tell the user what was stored
- tell the user what follow-up questions were created
- if storage failed, tell the user what was queued
