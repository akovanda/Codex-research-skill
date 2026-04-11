# Implicit Research Capture

This document describes the default Codex workflow for research intent.

Primary pieces:

- [`skills/research-capture`](../skills/research-capture/SKILL.md)
- [`skills/research-memory-retrieval`](../skills/research-memory-retrieval/SKILL.md)
- local queue fallback
- backend selection with localhost default and optional shared backend overrides

## Behavior

When a request is clearly research-shaped, the capture workflow should:

1. search the registry first
2. reuse fresh guidance when it already covers the question
3. perform new source-backed research when needed
4. store private question/session/excerpt/claim/report artifacts
5. create follow-up questions for gaps, needs, or wants
6. queue the capture if the backend is temporarily unavailable

Memory/retrieval research routes to the specialist skill and still writes into the same registry model.

## Backend selection

Precedence:

1. `RESEARCH_REGISTRY_BACKEND_URL`
2. `RESEARCH_REGISTRY_BACKEND_PROFILE`
3. org profile matched by `RESEARCH_REGISTRY_ORG`
4. `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`
5. localhost default

When no remote backend is configured, local skill use stays on the embedded local backend.

## Queue

Inspect pending bundles:

```bash
. .venv/bin/activate
research-registry-capture-queue list
```

Replay pending bundles:

```bash
. .venv/bin/activate
research-registry-capture-queue flush
```

## Expected summary shape

Implicit capture summaries should carry:

- current guidance
- evidence that supports it right now
- gaps
- needs
- wants
- follow-up questions
- registry ids for the stored or reused artifacts
