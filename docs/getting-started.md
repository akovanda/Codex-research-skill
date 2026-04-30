# Getting Started

This is the fastest path for a new user who wants Research Registry working locally with Codex.

If you want a quick answer to "what does this install change?" or "do I need Docker?", read [FAQ](faq.md) first.

The intended first run is:

1. run `make up`
2. verify the app, MCP wiring, and API docs with `make status`
3. open the UI or `/docs`
4. ask Codex to do source-backed research and let the implicit capture flow store it

## Prerequisites

- Python 3.12
- Docker with Compose support
- Codex on the same machine if you want the managed MCP setup

If your host `python3` is older than 3.12, either run `make up` with `PYTHON=python3.12` or precreate `.venv` with a 3.12 interpreter before using `make`.

## Install the local runtime

From the repo root:

```bash
make up
```

That command does seven things for you:

- creates `.venv/` if needed
- installs the project in editable mode
- creates managed config under `~/.config/research-registry/`
- starts Postgres plus the app on `http://127.0.0.1:8010`
- writes a managed `researchRegistry` MCP block into `~/.codex/config.toml`
- installs the research skills into `~/.codex/skills/`
- seeds demo content by default so the UI is not empty

If you need to bootstrap `.venv` on a machine where `python3` is too old, a user-local `uv` install works:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv python install 3.12
~/.local/bin/uv venv --python 3.12 .venv
make up
```

`make up` will reuse the precreated `.venv`.

If you want the runtime without demo content:

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

## Verify that it worked

Run:

```bash
make status
curl http://127.0.0.1:8010/readyz
curl http://127.0.0.1:8010/openapi.json
```

You want to see:

- `configured=true`
- `ready=true`
- `api_key_configured=true`
- `codex_mcp_managed=true`
- `{"status":"ready"}` from `/readyz`
- OpenAPI JSON from `/openapi.json`

If the local install patched Codex correctly, `~/.codex/config.toml` now points at:

- `http://127.0.0.1:8010/mcp/`

If you already had Codex sessions open during the first install, restart them so they reload the managed MCP block and the newly installed `research-capture` and `research-memory-retrieval` skills.

## Make the UI useful immediately

A brand-new registry is empty until something is captured or seeded.

`make up` already seeds demo content by default. Then open `http://127.0.0.1:8010` in a browser. You should see published reports, claims, and questions instead of a blank board.

## First real workflow

Once the local runtime is working, the normal flow is:

1. Ask Codex to research something that should be source-backed.
2. Let the registry search existing material first.
3. Let new research store private questions, sessions, excerpts, claims, and a report when reuse is not enough.
4. Open the workspace at `http://127.0.0.1:8010/admin/login`.
5. Review the private records and publish the reusable ones.

If you used `make up`, the admin token is stored in:

- `~/.config/research-registry/config.toml`

You can print the current managed token and API key with:

```bash
make token
```

## Good first prompts

- `Please research evaluation design for long-term memory retrieval and store the results.`
- `Investigate reranking strategies for agent memory retrieval and keep source-backed notes.`
- `Compare approaches for long-context memory compression and store a reusable report.`

## Common issues

Docker is not running:

- start Docker and rerun `make up`

Port `8010` is already in use:

- stop the existing service with `make down`
- or use the manual installer path with `research-registry-local-install --port 8011`

Codex already has a manual `researchRegistry` MCP entry:

- remove or rename the manual block in `~/.codex/config.toml`
- rerun `make up`

Your host `python3` is older than 3.12:

- rerun with `PYTHON=python3.12 make up`
- or precreate `.venv` with `uv venv --python 3.12 .venv` and rerun `make up`

You want to stop the local stack:

```bash
make down
```

You want to remove the managed local integration:

```bash
make uninstall
```

You want to remove the managed runtime plus its local data:

```bash
make purge-local
```

## Next docs

- [Local deployment](deploy-local.md)
- [Implicit research capture](implicit-research-capture.md)
- [Memory/retrieval skill](memory-retrieval-skill.md)
- [Shared deployment with Compose](deploy-shared-compose.md)
