# Local Deployment

Local deployment is the default and requires no external services.

## Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
export RESEARCH_REGISTRY_ADMIN_TOKEN=change-me
export RESEARCH_REGISTRY_SESSION_SECRET=change-me-too
research-registry-web
```

The app listens on `http://127.0.0.1:8000` by default.

## Storage

Default local storage:

- `RESEARCH_REGISTRY_DATABASE_URL=sqlite:///<repo>/.data/registry.sqlite3`

Compatibility fallback:

- `RESEARCH_REGISTRY_DB_PATH=.data/registry.sqlite3`

## Health

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

## Optional setup

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

- local Codex and MCP workflows still default to localhost when no remote backend override is configured
- local mode is the recommended first-run path for contributors and new users
