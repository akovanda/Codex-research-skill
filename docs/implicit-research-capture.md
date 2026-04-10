# Implicit Research Capture

This document covers the general `research-capture` skill, the private-by-default research storage flow, and the queue fallback for backend outages.

## What It Adds

- a global implicit skill at [`skills/research-capture`](/home/akovanda/dev/llmresearch/skills/research-capture)
- topic delegation to [`skills/research-memory-retrieval`](/home/akovanda/dev/llmresearch/skills/research-memory-retrieval)
- a local queue for pending research captures
- a queue CLI for inspect and replay
- hosted-default backend selection with explicit custom and org overrides
- per-user or per-org namespace routing for queued replay

## Install The Skill Globally

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -sfn "/home/akovanda/dev/llmresearch/skills/research-capture" "${CODEX_HOME:-$HOME/.codex}/skills/research-capture"
```

The memory skill can remain installed as a separate delegated skill:

```bash
ln -sfn "/home/akovanda/dev/llmresearch/skills/research-memory-retrieval" "${CODEX_HOME:-$HOME/.codex}/skills/research-memory-retrieval"
```

## Queue Behavior

Backend selection precedence for MCP and queue replay:

1. `RESEARCH_REGISTRY_BACKEND_URL`
2. `RESEARCH_REGISTRY_BACKEND_PROFILE`
3. org profile matched by `RESEARCH_REGISTRY_ORG`
4. `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`
5. embedded local backend when none of the above are set

Queued bundles are scoped to the selected backend and namespace so a bundle created for one org or backend is not replayed into another by mistake.

Default queue path:

```bash
${RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH:-/home/akovanda/dev/llmresearch/.data/pending-research-captures.jsonl}
```

Inspect:

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
research-registry-capture-queue list
```

Replay:

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
research-registry-capture-queue flush
```

## Validate

```bash
cd /home/akovanda/dev/llmresearch
. .venv/bin/activate
pytest -q
python3 /home/akovanda/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/akovanda/dev/llmresearch/skills/research-capture
python3 /home/akovanda/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/akovanda/dev/llmresearch/skills/research-memory-retrieval
```

## Expected Behavior

- research-shaped requests trigger `research-capture`
- memory/retrieval research routes to `research-memory-retrieval`
- registry content is searched before new storage
- new research stores private annotations, findings, and a report
- remote writes use API keys and preserve namespace plus actor attribution
- self-published artifacts appear in their public namespace before they are promoted into the shared global index
- if storage is unavailable, a queue bundle is written and replayed later
- the user gets an explicit summary of reuse, storage, or queue status
