# Getting Started

This is the fastest path for a new user who wants Research Registry working locally with Codex.

The intended first run is:

1. install the managed localhost runtime
2. verify that the app and MCP endpoint are healthy
3. seed demo data so the UI is not empty
4. ask Codex to do source-backed research and let the implicit capture flow store it

## Prerequisites

- Python 3.12
- Docker with Compose support
- Codex on the same machine if you want the managed MCP setup

## Install the local runtime

From the repo root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
research-registry-local-install
```

That command does four things for you:

- creates managed config under `~/.config/research-registry/`
- starts Postgres plus the app on `http://127.0.0.1:8010`
- writes a managed `researchRegistry` MCP block into `~/.codex/config.toml`
- installs the research skills into `~/.codex/skills/`

## Verify that it worked

Run:

```bash
. .venv/bin/activate
research-registry-local-status
curl http://127.0.0.1:8010/readyz
```

You want to see:

- `configured=true`
- `ready=true`
- `api_key_configured=true`
- `codex_mcp_managed=true`
- `{"status":"ready"}` from `/readyz`

If the local install patched Codex correctly, `~/.codex/config.toml` now points at:

- `http://127.0.0.1:8010/mcp/`

## Make the UI useful immediately

A brand-new registry is empty until something is captured or seeded.

For a first walkthrough, load the demo content:

```bash
. .venv/bin/activate
research-registry-seed
research-registry-seed-memory-retrieval
```

Then open `http://127.0.0.1:8010` in a browser. You should see published reports, claims, and questions instead of a blank board.

## First real workflow

Once the local runtime is working, the normal flow is:

1. Ask Codex to research something that should be source-backed.
2. Let the registry search existing material first.
3. Let new research store private questions, sessions, excerpts, claims, and a report when reuse is not enough.
4. Open the workspace at `http://127.0.0.1:8010/admin/login`.
5. Review the private records and publish the reusable ones.

If you used `research-registry-local-install`, the admin token is stored in:

- `~/.config/research-registry/config.toml`

## Good first prompts

- `Please research evaluation design for long-term memory retrieval and store the results.`
- `Investigate reranking strategies for agent memory retrieval and keep source-backed notes.`
- `Compare approaches for long-context memory compression and store a reusable report.`

## Common issues

Docker is not running:

- start Docker and rerun `research-registry-local-install`

Port `8010` is already in use:

- stop the existing service with `research-registry-local-stop`
- or install on a different port with `research-registry-local-install --port 8011`

Codex already has a manual `researchRegistry` MCP entry:

- remove or rename the manual block in `~/.codex/config.toml`
- rerun `research-registry-local-install`

You want to stop the local stack:

```bash
. .venv/bin/activate
research-registry-local-stop
```

## Next docs

- [Local deployment](deploy-local.md)
- [Implicit research capture](implicit-research-capture.md)
- [Memory/retrieval skill](memory-retrieval-skill.md)
- [Shared deployment with Compose](deploy-shared-compose.md)
