# Local Deployment

Local deployment is the default. The recommended path for real use is one shared localhost service for all of your local Codex instances.

If you are brand new to the project, read [Getting Started](getting-started.md) first.

## Prerequisites

- Python 3.12
- Docker with Compose support
- Codex on the same machine if you want the managed MCP wiring

## Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
research-registry-local-install
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
research-registry-local-status
curl http://127.0.0.1:8010/readyz
```

What good looks like:

- `configured=true`
- `ready=true`
- `api_key_configured=true`
- `codex_mcp_managed=true`
- `GET /readyz` returns `{"status":"ready"}`

Check status:

```bash
research-registry-local-status
```

Stop the local stack:

```bash
research-registry-local-stop
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

## Health

```bash
curl http://127.0.0.1:8010/healthz
curl http://127.0.0.1:8010/readyz
```

## Optional setup

Put useful content into a new registry:

```bash
research-registry-seed
research-registry-seed-memory-retrieval
```

Then open `http://127.0.0.1:8010` and confirm the public board is populated.

Seed demo content:

```bash
research-registry-seed
research-registry-seed-memory-retrieval
```

Run migrations explicitly:

```bash
research-registry-migrate
```

## Notes

- local Codex and MCP workflows default to localhost when no remote backend override is configured
- the managed MCP endpoint is HTTP-first; the stdio MCP server remains available for compatibility
- local mode is the recommended first-run path for contributors and new users
- if you need a repo-local no-Docker process, use `research-registry-web` and treat that path as development-only for this release
- if you used `research-registry-local-install`, the admin token is stored in `~/.config/research-registry/config.toml`
