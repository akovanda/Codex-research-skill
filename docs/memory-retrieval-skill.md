# Memory/Retrieval Skill

This document covers the `research-memory-retrieval` Codex skill and its local validation flow.

If you are just trying to get the project running, start with [Getting Started](getting-started.md).

## Files

- [`skills/research-memory-retrieval/SKILL.md`](../skills/research-memory-retrieval/SKILL.md)
- [`skills/research-capture/SKILL.md`](../skills/research-capture/SKILL.md)

## Install locally

```bash
make up
```

`make up` installs both managed skill symlinks into `~/.codex/skills/` and starts the default localhost backend.

## Start the local backend

```bash
make status
```

This gives the skill a shared localhost backend plus a managed MCP endpoint in `~/.codex/config.toml`.

## Seed the memory/retrieval corpus

```bash
./.venv/bin/research-registry-seed-memory-retrieval
```

`make up` already runs this seed step by default. Rerun it manually only if you want to refresh the demo corpus.

## Validate

```bash
make test
make workflow-check
make grounded-pass-check
```

`make workflow-check` constrains the harnesses to the current repo for fast deterministic validation. `make grounded-pass-check` is the deeper built-in example run that writes `.data/research-pass-runner.md`.

## Expected behavior

- the skill searches existing registry content first
- fresh guidance is reused before new storage is created
- new work stores excerpts, claims, and a guidance report
- unsupported claims are not deposited
- summaries preserve reusable guidance, current evidence, and follow-up questions
- refresh can create successor reports plus follow-up questions when newer local evidence exists
