# Research Registry

Research Registry is a **local-first research memory for humans and agents**. It stores a research question, the evidence collected for it, the claims supported by that evidence, and a reusable guidance report on top.

This repo is currently a **developer preview** aimed at:

- a single developer running on `localhost`
- a small team sharing one self-hosted registry
- Codex and MCP workflows that want durable, source-backed research memory

The future public/shared network is not the current product target. The current target is **usable local-first software** with a clear path to **self-hosted shared org deployments**.

## Core Model

Canonical records:

- `Question`
- `ResearchSession`
- `Source`
- `Excerpt`
- `Claim`
- `Report`

Reports are guidance-first. They carry:

- current guidance
- evidence that supports it right now
- gaps
- needs
- wants
- linked follow-up questions

Legacy aliases such as `annotation` and `finding` still exist for compatibility, but they are not the canonical model for new integrations.

## Quick Start

### Local default

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
research-registry-local-install
```

This installs a shared localhost runtime for your local Codex instances:

- Docker Compose on `127.0.0.1:8010`
- Postgres for durable local storage
- a shared local API key
- a managed MCP entry in `~/.codex/config.toml` pointing at `http://127.0.0.1:8010/mcp/`
- symlinks for the `research-capture` and `research-memory-retrieval` skills in `~/.codex/skills`

Local default behavior:

- localhost HTTP backend
- Postgres storage inside Compose
- one shared local runtime for multiple Codex instances
- backend status resolves to localhost unless you explicitly point clients elsewhere

Status and stop commands:

```bash
. .venv/bin/activate
research-registry-local-status
research-registry-local-stop
```

For a repo-local developer-only run without Docker, `research-registry-web` still works and defaults to local SQLite.

Optional demo data:

```bash
. .venv/bin/activate
research-registry-seed
research-registry-seed-memory-retrieval
```

### Shared self-hosted mode

Use Postgres and point clients at a shared server:

- [Compose deployment](docs/deploy-shared-compose.md)
- [Kubernetes deployment](docs/deploy-kubernetes.md)

## Configuration

Canonical server/runtime settings:

- `RESEARCH_REGISTRY_DATABASE_URL`
- `RESEARCH_REGISTRY_ADMIN_TOKEN`
- `RESEARCH_REGISTRY_SESSION_SECRET`
- `RESEARCH_REGISTRY_HOST`
- `RESEARCH_REGISTRY_PORT`
- `RESEARCH_REGISTRY_PUBLIC_BASE_URL`
- `RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH`
- `RESEARCH_REGISTRY_BACKEND_PROFILE_PATH`

Client/backend-selection settings:

- `RESEARCH_REGISTRY_BACKEND_URL`
- `RESEARCH_REGISTRY_BACKEND_PROFILE`
- `RESEARCH_REGISTRY_API_KEY`
- `RESEARCH_REGISTRY_ORG`
- `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`

Compatibility fallback:

- `RESEARCH_REGISTRY_DB_PATH` remains supported for local SQLite setups. If `RESEARCH_REGISTRY_DATABASE_URL` is unset, the app derives a local SQLite URL from that path.

Backend selection precedence for clients:

1. `RESEARCH_REGISTRY_BACKEND_URL`
2. `RESEARCH_REGISTRY_BACKEND_PROFILE`
3. org profile matched by `RESEARCH_REGISTRY_ORG`
4. `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`
5. localhost default

## Health And Bootstrap

Health endpoints:

- `GET /healthz` for process liveness
- `GET /readyz` for storage readiness

Admin bootstrap endpoints:

- `POST /api/admin/organizations`
- `POST /api/admin/api-keys`

These are guarded by the admin token and are intended for self-hosted setup workflows.

## Canonical API Surface

Public reads:

- `GET /api/search`
- `GET /api/backend/status`
- `GET /api/questions/{id}`
- `GET /api/sessions/{id}`
- `GET /api/sources/{id}`
- `GET /api/excerpts/{id}`
- `GET /api/claims/{id}`
- `GET /api/reports/{id}`

Authenticated writes:

- `POST /api/questions`
- `POST /api/questions/{id}/status`
- `POST /api/sessions`
- `POST /api/sources`
- `POST /api/excerpts`
- `POST /api/claims`
- `POST /api/reports`
- `POST /api/publish`

Admin moderation:

- `POST /api/review`
- `POST /api/index-state`

Compatibility aliases:

- `/api/annotations/{id}` maps to excerpts
- `/api/findings/{id}` maps to claims

## MCP And Skills

The web app and API are the primary product surface. MCP and Codex skills sit on top of that:

- HTTP MCP endpoint: `http://127.0.0.1:8010/mcp/` after `research-registry-local-install`
- stdio MCP server: `research-registry-mcp`
- implicit capture skill: [`skills/research-capture`](skills/research-capture/SKILL.md)
- memory/retrieval skill: [`skills/research-memory-retrieval`](skills/research-memory-retrieval/SKILL.md)

## Deployment

- [Architecture](docs/architecture.md)
- [Local deployment](docs/deploy-local.md)
- [Shared Compose deployment](docs/deploy-shared-compose.md)
- [Kubernetes deployment](docs/deploy-kubernetes.md)
- [Implicit research capture](docs/implicit-research-capture.md)
- [Memory/retrieval skill](docs/memory-retrieval-skill.md)
- [Research pass suite](docs/research-pass-suite.md)

Container assets:

- `Dockerfile`
- `deploy/compose.yaml`
- `deploy/kubernetes/`

## Developer Tooling

Migrate storage explicitly:

```bash
. .venv/bin/activate
research-registry-migrate
```

Run tests:

```bash
. .venv/bin/activate
pytest -q
```

Install the shared localhost runtime:

```bash
. .venv/bin/activate
research-registry-local-install
```

Run the grounded pass runner:

```bash
. .venv/bin/activate
research-registry-pass-runner --db-path /tmp/research-pass-runner.sqlite3 --reset --rounds 2
```

## Preview Notes

- Localhost is the default.
- Shared org mode is self-hosted, not multi-tenant cloud.
- API keys plus admin token are the supported auth model for this preview.
- Postgres is the intended backend for shared deployments.
