# Research Registry

Research Registry is a small MVP for source-backed AI research memory. It stores source-anchored annotations as the canonical unit, then derives findings and reports from those annotations. The same registry is exposed through:

- a FastAPI JSON API
- a public website for browse and inspection
- a lightweight MCP server for agent workflows

## What it implements

- Immutable core records: `Source`, `Annotation`, `Finding`, `Report`, and `Run`
- Private-by-default deposits with explicit publishing
- Source anchors with quote hashes and stable passage fingerprints
- Public search that favors provenance, review state, and source quality
- Derived report compilation that preserves links back to findings, annotations, and source URLs
- Token-gated admin writes for API and website curation

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
export RESEARCH_REGISTRY_ADMIN_TOKEN=change-me
research-registry-seed
research-registry-web
```

The web app runs at `http://127.0.0.1:8000`.

Public pages show only published records. Visit `/admin/login` and use the admin token to review and publish private records.

## Environment

- `RESEARCH_REGISTRY_DB_PATH`: SQLite path. Defaults to `.data/registry.sqlite3`
- `RESEARCH_REGISTRY_ADMIN_TOKEN`: enables admin API writes and admin web login
- `RESEARCH_REGISTRY_SESSION_SECRET`: session secret for admin login cookies
- `RESEARCH_REGISTRY_HOST`: web bind host, default `127.0.0.1`
- `RESEARCH_REGISTRY_PORT`: web bind port, default `8000`

If `RESEARCH_REGISTRY_ADMIN_TOKEN` is unset, the app runs in open local mode: write operations and admin pages are not blocked. That is useful for local exploration but not safe for deployment.

## API Surface

Public reads:

- `GET /healthz`
- `GET /api/search?q=...&kind=annotation|finding|report|source`
- `GET /api/sources/{id}`
- `GET /api/annotations/{id}`
- `GET /api/findings/{id}`
- `GET /api/reports/{id}`

Admin writes:

- `POST /api/runs`
- `POST /api/sources`
- `POST /api/annotations`
- `POST /api/findings`
- `POST /api/reports/compile`
- `POST /api/publish`
- `POST /api/review`

Admin JSON requests should include `X-Admin-Token: <token>` when a token is configured.

## MCP Server

Start the MCP server with stdio transport:

```bash
. .venv/bin/activate
research-registry-mcp
```

Exposed tools:

- `search`
- `create_run`
- `get_source`
- `get_annotation`
- `get_finding`
- `get_report`
- `add_annotation`
- `create_finding`
- `compile_report`
- `publish`

## Memory/Retrieval Skill

This repo now includes a reusable Codex skill at [`skills/research-memory-retrieval`](/home/akovanda/dev/llmresearch/skills/research-memory-retrieval) plus a domain seed script for memory/retrieval dry runs.

```bash
. .venv/bin/activate
research-registry-seed-memory-retrieval
```

See [`docs/memory-retrieval-skill.md`](/home/akovanda/dev/llmresearch/docs/memory-retrieval-skill.md) for install, dry-run, and validation steps.

## Testing

```bash
. .venv/bin/activate
pytest
```
