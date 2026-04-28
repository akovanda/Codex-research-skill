# Repo-Aware Capture

Use the repo-aware branch when the user asks for:

- the exact command for a file
- triage on a failing test, stack trace, or local error log
- preflight/setup diagnosis before running commands
- review notes, likely reviewer concerns, or risk areas for changed files

Check this local evidence in order:

1. `.codex/repo-profile.toml`
2. the nearest applicable `AGENTS.md` files
3. local manifests and config files
4. targeted `rg` hits
5. git status, diff, or recent history
6. coverage artifacts

The stored report should surface:

- affected area
- instructions found
- evidence checked
- commands recommended
- blockers
- likely hypotheses
- reviewer notes
- coverage follow-up
- registry state
