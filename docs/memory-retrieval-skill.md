# Memory/Retrieval Skill

This document covers the local install and dry-run path for the `research-memory-retrieval` Codex skill.

It can be used directly or as a delegated specialist behind the general `research-capture` skill.

## What It Needs

- the skill folder at [`skills/research-memory-retrieval`](/home/akovanda/dev/llmresearch/skills/research-memory-retrieval)
- a Research Registry backend
- the Research Registry MCP server

## Install The Skill Globally

Create a symlink so the repo copy stays versioned but Codex can discover it globally:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -sfn "/home/akovanda/dev/llmresearch/skills/research-memory-retrieval" "${CODEX_HOME:-$HOME/.codex}/skills/research-memory-retrieval"
```

## Seed The Memory/Retrieval Corpus

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
research-registry-seed-memory-retrieval
```

This creates public seed artifacts for:

- retrieval followed by reranking
- stale indexes and provenance failures in memory retrieval

## Start The Backend

Web app:

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
research-registry-web
```

MCP server:

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
research-registry-mcp
```

## Dry-Run Checks

Validate the repo baseline:

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
pytest -q
```

Validate the skill package:

```bash
python3 /home/akovanda/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/akovanda/dev/llmresearch/skills/research-memory-retrieval
```

Expected skill exercise prompts:

- `Use $research-memory-retrieval to research vector retrieval vs reranking and reuse anything already in memory.`
- `Use $research-memory-retrieval to investigate stale indexes and memory retrieval failures.`
- `Use $research-memory-retrieval to research evaluation metrics for agent memory and deposit source-backed findings.`

Expected behavior:

- existing reranking content is found before any new deposition
- stale-index failure material can be synthesized into a report
- new agent-memory-evaluation work creates a run, annotations, and a finding
- unsupported claims are refused rather than deposited
