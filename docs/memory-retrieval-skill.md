# Memory/Retrieval Skill

This document covers the `research-memory-retrieval` Codex skill and its local validation flow.

If you are just trying to get the project running, start with [Getting Started](getting-started.md).

## Files

- [`skills/research-memory-retrieval/SKILL.md`](../skills/research-memory-retrieval/SKILL.md)
- [`skills/research-capture/SKILL.md`](../skills/research-capture/SKILL.md)

## Install locally

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -sfn "$(pwd)/skills/research-memory-retrieval" "${CODEX_HOME:-$HOME/.codex}/skills/research-memory-retrieval"
```

## Start the local backend

```bash
. .venv/bin/activate
research-registry-local-install
```

This gives the skill a shared localhost backend plus a managed MCP endpoint in `~/.codex/config.toml`.

## Seed the memory/retrieval corpus

```bash
. .venv/bin/activate
research-registry-seed-memory-retrieval
```

## Validate

```bash
. .venv/bin/activate
pytest -q
research-registry-memory-retrieval-harness --scenario reuse-optimization
research-registry-memory-retrieval-harness --scenario synthesis-failures
research-registry-memory-retrieval-harness --scenario gap-fill-metrics
```

## Expected behavior

- the skill searches existing registry content first
- fresh guidance is reused before new storage is created
- new work stores excerpts, claims, and a guidance report
- unsupported claims are not deposited
- summaries preserve reusable guidance, current evidence, and follow-up questions
