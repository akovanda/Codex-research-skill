# Local Deployment

Local deployment is the default. The recommended path for real use is one shared localhost service for all of your local Codex instances.

If you are brand new to the project, read [Getting Started](getting-started.md) first.

Preview support target:

- Linux: primary localhost target and CI-covered
- macOS: intended localhost preview target
- Windows: not yet claimed

## Prerequisites

- Python 3.12
- Docker with Compose support
- Codex on the same machine if you want the managed MCP wiring

## Start

```bash
make up
```

`make up` creates `.venv/`, installs the repo in editable mode, starts the managed localhost runtime, and seeds demo content by default.

If you want the runtime without demo content:

```bash
make up SEED_DEMO=0
```

This creates:

- managed config under `~/.config/research-registry/`
- managed data under `~/.local/share/research-registry/`
- a Docker Compose stack on `http://127.0.0.1:8010`
- a managed MCP entry in `~/.codex/config.toml`
- skill symlinks in `~/.codex/skills/`

## Verify

Check status:

```bash
make status
curl http://127.0.0.1:8010/readyz
curl http://127.0.0.1:8010/openapi.json
```

What good looks like:

- `configured=true`
- `ready=true`
- `api_key_configured=true`
- `codex_mcp_managed=true`
- `GET /readyz` returns `{"status":"ready"}`
- `GET /openapi.json` returns the generated OpenAPI document

Print the managed local token values:

```bash
make token
```

Stop the local stack:

```bash
make down
```

Remove the managed Codex integration but keep local data:

```bash
make uninstall
```

Remove the managed runtime, local data, and Docker volumes:

```bash
make purge-local
```

Restore the previous Codex config from the managed backup instead of only removing the managed block:

```bash
./.venv/bin/research-registry-local-uninstall --restore-codex-backup
```

## Runtime details

Managed local defaults:

- HTTP app: `http://127.0.0.1:8010`
- HTTP MCP: `http://127.0.0.1:8010/mcp/`
- storage: Postgres inside Docker Compose
- auth: admin token plus a shared local API key written into the managed config and Codex MCP block

The generated runtime files live under `~/.config/research-registry/`:

- `config.toml`
- `compose.yaml`
- `.env`

## Storage

The recommended local runtime uses Postgres in Compose.

For a repo-local developer-only process without Docker, `research-registry-web` still supports SQLite:

- `RESEARCH_REGISTRY_DATABASE_URL=sqlite:///<repo>/.data/registry.sqlite3`

Compatibility fallback:

- `RESEARCH_REGISTRY_DB_PATH=.data/registry.sqlite3`

Config examples:

- repo root `.env.example` is the repo-local development example and defaults to SQLite
- `deploy/.env.example` is the shared Compose example and defaults to Postgres plus bind/public URL settings

## Health

```bash
curl http://127.0.0.1:8010/healthz
curl http://127.0.0.1:8010/readyz
```

## Optional setup

Put useful content into a new registry:

```bash
make up
```

Then open `http://127.0.0.1:8010` and confirm the public board is populated.

Seed demo content:

```bash
./.venv/bin/research-registry-seed
./.venv/bin/research-registry-seed-memory-retrieval
```

Run migrations explicitly:

```bash
./.venv/bin/research-registry-migrate
```

## Notes

- local Codex and MCP workflows default to localhost when no remote backend override is configured
- the managed MCP endpoint is HTTP-first; the stdio MCP server remains available for compatibility
- local mode is the recommended first-run path for contributors and new users
- if you need a repo-local no-Docker process, use `research-registry-web` and treat that path as development-only for this release
- if you used `make up`, the admin token is stored in `~/.config/research-registry/config.toml`
