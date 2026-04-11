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
- Hosted-default backend selection with explicit custom or corporate overrides
- API-key writes with per-user or per-org namespaces
- Public namespace browsing separated from the shared global index
- Derived report compilation that preserves links back to findings, annotations, and source URLs
- API-key writes plus admin moderation for review and global-index curation
- Implicit specialist routing for memory/retrieval, inference optimization, and LLM eval topics

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

The web app runs two public surfaces:

- `/` shows the shared global index only
- `/public/{namespace}` shows everything published in that user or org namespace, even if it is not promoted into the shared global index

Visit `/admin/login` and use the admin token to review private records, moderate published artifacts, and promote items into the shared global index.

## Environment

- `RESEARCH_REGISTRY_DB_PATH`: SQLite path. Defaults to `.data/registry.sqlite3`
- `RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH`: queued implicit-capture path. Defaults to `.data/pending-research-captures.jsonl`
- `RESEARCH_REGISTRY_BACKEND_PROFILE_PATH`: JSON profile file for named or org backends. Defaults to `.data/backend-profiles.json`
- `RESEARCH_REGISTRY_ADMIN_TOKEN`: enables admin API writes and admin web login
- `RESEARCH_REGISTRY_SESSION_SECRET`: session secret for admin login cookies
- `RESEARCH_REGISTRY_HOST`: web bind host, default `127.0.0.1`
- `RESEARCH_REGISTRY_PORT`: web bind port, default `8000`
- `RESEARCH_REGISTRY_PUBLIC_BASE_URL`: canonical URL for this backend, used in backend-status responses
- `RESEARCH_REGISTRY_DEFAULT_BACKEND_URL`: hosted default backend URL used by MCP/queue clients when no override is set
- `RESEARCH_REGISTRY_BACKEND_URL`: explicit backend override, higher priority than org or hosted default
- `RESEARCH_REGISTRY_BACKEND_PROFILE`: named backend profile from the profile JSON
- `RESEARCH_REGISTRY_API_KEY`: API key used by MCP or queue clients for remote writes
- `RESEARCH_REGISTRY_ORG`: org namespace hint, used for org-profile resolution and org-scoped keys

Local development default:

- if no backend override/profile/default is set, MCP and queue clients present the backend as `RESEARCH_REGISTRY_PUBLIC_BASE_URL` and default that to `http://127.0.0.1:8000`
- that localhost default still uses the embedded local service directly, so local skill work does not require a running HTTP server or an API key

If `RESEARCH_REGISTRY_ADMIN_TOKEN` is unset, the app runs in open local mode: write operations and admin pages are not blocked. That is useful for local exploration but not safe for deployment.

## API Surface

Public reads:

- `GET /healthz`
- `GET /api/search?q=...&kind=annotation|finding|report|source`
- `GET /api/backend/status`
- `GET /api/sources/{id}`
- `GET /api/annotations/{id}`
- `GET /api/findings/{id}`
- `GET /api/reports/{id}`

Authenticated writes:

- `POST /api/runs`
- `POST /api/sources`
- `POST /api/annotations`
- `POST /api/findings`
- `POST /api/reports`
- `POST /api/reports/compile`
- `POST /api/publish`

Admin moderation:

- `POST /api/review`
- `POST /api/index-state`

Write requests should include `X-API-Key: <token>`. Admin JSON requests can still use `X-Admin-Token: <token>` for local moderation workflows.

## MCP Server

Start the MCP server with stdio transport:

```bash
. .venv/bin/activate
research-registry-mcp
```

Exposed tools:

- `search`
- `backend_status`
- `create_run`
- `get_source`
- `get_annotation`
- `get_finding`
- `get_report`
- `add_annotation`
- `create_finding`
- `create_report`
- `compile_report`
- `publish`

## Memory/Retrieval Skill

This repo now includes a reusable Codex skill at [`skills/research-memory-retrieval`](/home/akovanda/dev/llmresearch/skills/research-memory-retrieval) plus a domain seed script for memory/retrieval dry runs.

```bash
. .venv/bin/activate
research-registry-seed-memory-retrieval
research-registry-memory-retrieval-harness --scenario reuse-optimization
```

See [`docs/memory-retrieval-skill.md`](/home/akovanda/dev/llmresearch/docs/memory-retrieval-skill.md) for install, dry-run, and validation steps.

## Specialist Domain Harness

The implicit capture path also includes built-in specialist harnesses for:

- memory/retrieval
- inference optimization
- LLM evals

Run the broader harness directly:

```bash
. .venv/bin/activate
research-registry-domain-harness --scenario inference-reuse
research-registry-domain-harness --scenario evals-gap-fill
```

## Implicit Research Capture

This repo also includes a general implicit research skill at [`skills/research-capture`](/home/akovanda/dev/llmresearch/skills/research-capture). It is designed to trigger on research intent, store research privately by default, and queue captures locally when the registry path is unavailable.

For memory/retrieval, inference optimization, and LLM eval topics, the implicit path now routes through tested specialist harnesses. Memory uses the explicit [`skills/research-memory-retrieval`](/home/akovanda/dev/llmresearch/skills/research-memory-retrieval) flow directly, while the other domains use the same reuse vs synthesis vs gap-fill contract internally.

Queue inspection and replay:

```bash
. .venv/bin/activate
research-registry-capture-queue list
research-registry-capture-queue flush
```

See [`docs/implicit-research-capture.md`](/home/akovanda/dev/llmresearch/docs/implicit-research-capture.md) for install and behavior details.

## Real Research Passes

This repo includes a grounded pass suite based on the current long-memory project work in `dnd2`, `continuity-benchmarks`, `continuity-core`, and `choose-game`.

```bash
. .venv/bin/activate
research-registry-pass-suite
research-registry-pass-suite --check-routing
research-registry-pass-suite --format markdown
```

See [`docs/research-pass-suite.md`](/home/akovanda/dev/llmresearch/docs/research-pass-suite.md) for the intended workflow.

## Testing

```bash
. .venv/bin/activate
pytest
```
