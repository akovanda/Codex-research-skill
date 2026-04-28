# Repo-Aware Capture

This document covers the repo-aware extension layer inside implicit capture.

Use this path when Codex is asked to:

- find the exact command for a file or path
- triage a failing test, stack trace, or local error log
- run preflight/setup diagnosis before commands
- review a change for risk areas and reviewer concerns

## Repo Profile

If a repo contains `.codex/repo-profile.toml`, repo-aware capture loads it first.

The profile defines:

- path globs for repo areas
- scoped test, lint, and build commands
- required tools and required local paths
- coverage artifact locations
- review conventions and likely owners

This repo ships a working example at [`.codex/repo-profile.toml`](../.codex/repo-profile.toml).

## Local Evidence Order

Repo-aware capture checks local evidence in this order:

1. `.codex/repo-profile.toml`
2. nearest applicable `AGENTS.md` files
3. local manifests and config files
4. targeted `rg` hits
5. git status, diff, or recent history
6. coverage artifacts

It stores the result in the same registry model as normal research capture.

## Stored Report Shape

Repo-aware reports use these sections:

- `Affected Area`
- `Instructions Found`
- `Evidence Checked`
- `Commands Recommended`
- `Blockers`
- `Likely Hypotheses`
- `Reviewer Notes`
- `Coverage Follow-up`
- `Registry State`

## Validation

Run the repo-aware test slice:

```bash
./.venv/bin/pytest -q tests/test_repo_intelligence.py
```

Run the full suite:

```bash
make test
```
