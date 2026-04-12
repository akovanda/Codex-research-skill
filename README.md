# Research Registry

Research Registry is a **local-first research memory for humans and agents**. It stores a research question, the evidence collected for it, the claims supported by that evidence, and a reusable guidance report on top.

This repo is currently a **developer preview** aimed at:

- a single developer running on `localhost`
- a small team sharing one self-hosted registry
- Codex and MCP workflows that want durable, source-backed research memory

The future public/shared network is not the current product target. The current target is **usable local-first software** with a clear path to **self-hosted shared org deployments**.

## Release Scope

`v0.1.0` is a **GitHub-first open-source preview**.

Release-critical supported paths:

- managed localhost runtime for multiple local Codex instances
- shared self-hosted Compose deployment for internal teams

Supported-but-secondary:

- repo-local developer process via `research-registry-web`
- stdio MCP via `research-registry-mcp`

Example-only or explicitly unsupported in this preview:

- Kubernetes as a production-hardened deployment target
- direct public-internet exposure without your own network controls
- PyPI as the primary install path
- published hosted multi-tenant service

Managed localhost preview support matrix:

- Linux: primary target and covered in CI smoke jobs
- macOS: intended supported preview target for localhost use, but not yet CI-covered
- Windows: not yet claimed for this preview

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

If you only want the fastest path that should work on a fresh machine, start with [Getting Started](docs/getting-started.md).

### Local default

```bash
make up
```

Verify that the local runtime is healthy:

```bash
make status
curl http://127.0.0.1:8010/readyz
curl http://127.0.0.1:8010/openapi.json
```

What success looks like:

- `make status` prints `configured=true` and `ready=true`
- `GET /readyz` returns `{"status":"ready"}`
- `GET /openapi.json` returns the Research Registry OpenAPI document
- `~/.codex/config.toml` contains a managed `researchRegistry` MCP block
- `~/.codex/skills/` contains `research-capture` and `research-memory-retrieval`

Then open `http://127.0.0.1:8010` in a browser. You should see published reports and claims instead of an empty board.

What `make up` does:

- creates `.venv/` if needed
- installs the project in editable mode
- creates managed config under `~/.config/research-registry/`
- creates managed data under `~/.local/share/research-registry/`
- starts the managed localhost Postgres plus app stack on `127.0.0.1:8010`
- patches `~/.codex/config.toml` with a managed `researchRegistry` MCP block
- installs the `research-capture` and `research-memory-retrieval` skill symlinks into `~/.codex/skills/`
- seeds demo content by default so the UI is not empty

If you want the stack without demo content:

```bash
make up SEED_DEMO=0
```

Manual equivalent:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
research-registry-local-install
research-registry-seed
research-registry-seed-memory-retrieval
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
make status
make token
make down
make uninstall
```

To remove the managed localhost install and delete its local config/data directories plus Docker volumes:

```bash
make purge-local
```

For a repo-local developer-only run without Docker, `research-registry-web` still works and defaults to local SQLite.

Optional demo data:

```bash
make up
```

First real workflow:

1. Ask Codex to research something source-backed.
2. Let the implicit capture workflow store the question, excerpts, claims, and report privately.
3. Open `/admin/login` and review what was stored.
4. Publish the reusable reports or claims that should become visible on the public board.

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

Environment examples:

- repo root [`.env.example`](.env.example) is for repo-local or container-local development and defaults to SQLite
- [`deploy/.env.example`](deploy/.env.example) is for the shared Compose preview and defaults to Postgres plus bind/public URL settings

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

Developer preview API docs:

- Swagger UI: `http://127.0.0.1:8010/docs`
- OpenAPI JSON: `http://127.0.0.1:8010/openapi.json`
- step-by-step curl flow: [docs/api-quickstart.md](docs/api-quickstart.md)

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

- HTTP MCP endpoint: `http://127.0.0.1:8010/mcp/` after `make up` or `research-registry-local-install`
- stdio MCP server: `research-registry-mcp`
- implicit capture skill: [`skills/research-capture`](skills/research-capture/SKILL.md)
- memory/retrieval skill: [`skills/research-memory-retrieval`](skills/research-memory-retrieval/SKILL.md)

## Deployment

- [Getting started](docs/getting-started.md)
- [API quickstart](docs/api-quickstart.md)
- [Architecture](docs/architecture.md)
- [Local deployment](docs/deploy-local.md)
- [Shared Compose deployment](docs/deploy-shared-compose.md)
- [Kubernetes deployment](docs/deploy-kubernetes.md)
- [Operations](docs/operations.md)
- [Implicit research capture](docs/implicit-research-capture.md)
- [Memory/retrieval skill](docs/memory-retrieval-skill.md)
- [Research pass suite](docs/research-pass-suite.md)
- [Support policy](SUPPORT.md)

Container assets:

- `Dockerfile`
- `deploy/compose.yaml`
- `deploy/kubernetes/`

## Developer Tooling

Migrate storage explicitly:

```bash
make up SEED_DEMO=0
./.venv/bin/research-registry-migrate
```

Run tests:

```bash
make test
```

Run the preview release gate:

```bash
make preview-check
```

Run the grounded pass runner:

```bash
./.venv/bin/research-registry-pass-runner --db-path /tmp/research-pass-runner.sqlite3 --reset --rounds 2
```

## Preview Notes

- Localhost is the default.
- Shared org mode is self-hosted, not multi-tenant cloud.
- Shared deployments are supported for internal-only exposure behind normal network controls.
- API keys plus admin token are the supported auth model for this preview.
- Postgres is the intended backend for shared deployments.
