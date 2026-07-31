# Implicit Research Capture

This document describes the legacy broad implicit-capture compatibility
workflow. It is no longer installed by the default v2 Codex plugin.

New installs should use [Codex Plugin](codex-plugin.md): `research-recall` may
invoke implicitly but is read-only, while `research-deposit` requires explicit
invocation or user approval.

## Explicit compatibility flags

Heuristic local research, repository intelligence, specialist routing, broad
capture, legacy harnesses, and the heuristic brief/refresh service methods are
disabled by default. An existing installation may temporarily recover that
behavior by setting:

```bash
export RESEARCH_REGISTRY_LEGACY_HEURISTICS=1
```

The first heuristic use in a process emits one deprecation warning. New code
should not import these adapters; compatibility imports are also available
under `research_registry.legacy`.

The low-level v1 MCP tool surface is a separate compatibility contract. It is
hidden by default and can be restored with:

```bash
export RESEARCH_REGISTRY_MCP_LEGACY=1
```

V1 HTTP routes and database tables remain available. The default plugin and
normal service startup use the v2 surface and do not load heuristic modules.

For first-run local setup, start with [Getting Started](getting-started.md).

For embedded local storage, run `make init` once before relying on this legacy
workflow. Installing the focused plugin does not install these broad legacy
skills.

Primary pieces:

- [`skills/research-capture`](../skills/research-capture/SKILL.md)
- [`skills/research-memory-retrieval`](../skills/research-memory-retrieval/SKILL.md)
- [Repo-aware capture](repo-aware-capture.md)
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

When the request is repo-aware instead of broad research, the capture workflow should:

1. load `.codex/repo-profile.toml` when present
2. resolve the nearest applicable `AGENTS.md` files
3. inspect local manifests/configs, targeted `rg` hits, git state, and coverage artifacts
4. recommend the narrowest valid command for the affected path
5. store the triage or review result in the same registry model by default

## Backend selection

Precedence:

1. `RESEARCH_REGISTRY_BACKEND_URL`
2. `RESEARCH_REGISTRY_BACKEND_PROFILE`
3. org profile matched by `RESEARCH_REGISTRY_ORG`
4. `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`
5. localhost default

When no remote backend is configured, local skill use stays on the embedded
personal SQLite backend. An explicitly retained managed Postgres config
continues to select its shared localhost backend.

## Local Source Roots

Implicit local research does not assume any personal repo layout.

Source-root precedence:

1. explicit `source_roots` passed by the caller
2. `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS`
3. `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS_FILE`
4. current workspace root only

Example roots file:

```toml
paths = [
  "/path/to/repo-a",
  "/path/to/repo-b",
]

[roots]
frontend = "/path/to/frontend-monolith"
```

The default config-file location is `~/.config/research-registry/local-research-roots.toml` unless `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS_FILE` overrides it.

Useful local checks:

```bash
research-registry doctor
```

## Queue

The capture queue remains supported. New entries should use
`research-capture-queue/v2` envelopes containing an atomic
`research-deposit/v2` bundle. Existing legacy queue records remain replayable
during the compatibility window and are not deleted by this change.

Inspect pending bundles:

```bash
./.venv/bin/research-registry-capture-queue list
```

Replay pending bundles:

```bash
./.venv/bin/research-registry-capture-queue flush
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

## Removal criteria

Removal is not scheduled. The adapters may be removed only in a future major
release after all of the following are complete:

- a new accepted ADR explicitly approves removal;
- a published deprecation period and release notice have elapsed;
- v1 data export/import and migration verification remain available;
- legacy queue records have a tested conversion or replay path;
- the marked legacy regression suite is no longer needed for supported
  compatibility behavior;
- no v1 table is deleted as a side effect.

Until then, the adapters remain explicit-only compatibility code. They must not
return to default startup, the default plugin, or implicit skill routing.
