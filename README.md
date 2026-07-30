# Research Registry

Research Registry is a **local-first research memory for humans and agents**. It stores a research question, the evidence collected for it, the claims supported by that evidence, and a reusable guidance report on top.

This repo is currently a **developer preview** aimed at:

- a single developer running on `localhost`
- a small team sharing one self-hosted registry
- Codex and MCP workflows that want durable, source-backed research memory

The future public/shared network is not the current product target. The current target is **usable local-first software** with a clear path to **self-hosted shared org deployments**.

## Release Scope

`v0.1.0` is the **GitHub-first open-source preview**. The unreleased v2 draft
uses aligned package and plugin metadata `0.2.0a1`; no alpha tag or artifact
has been published.

The v2 gate currently remains alpha until the operator-only Postgres,
real-database migration, shared Compose, and security-review evidence is
complete. See [release status](docs/release-status.md), [evaluation and fixed
gates](docs/evaluation-and-release-gates.md), and [upgrade/rollback](docs/upgrade-and-rollback.md).

Release-critical supported paths:

- personal SQLite plus filesystem blobs over local STDIO MCP
- shared self-hosted Compose deployment for internal teams
- wheel, sdist, and editable package installs

Supported-but-secondary:

- optional loopback web review server
- retained Docker/Postgres localhost compatibility runtime
- repo-local developer process via `research-registry-web`
- stdio MCP via `research-registry-mcp`

Example-only or explicitly unsupported in this preview:

- Kubernetes as a production-hardened deployment target
- direct public-internet exposure without your own network controls
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

If you are deciding whether this preview is even the right shape for you, read [docs/faq.md](docs/faq.md) first.

### Choose Your Path

- New user who wants the no-Docker personal install: [docs/getting-started.md](docs/getting-started.md)
- Operator who wants local SQLite and backups: [docs/deploy-local.md](docs/deploy-local.md)
- Operator who wants a shared internal deployment: [docs/deploy-shared-compose.md](docs/deploy-shared-compose.md)
- API user who wants a copy-paste HTTP flow: [docs/api-quickstart.md](docs/api-quickstart.md)
- Codex user who wants focused recall and explicit deposit: [docs/codex-plugin.md](docs/codex-plugin.md)
- Existing user maintaining the legacy implicit capture path: [docs/implicit-research-capture.md](docs/implicit-research-capture.md)
- Repo-heavy user who wants command routing and triage: [docs/repo-aware-capture.md](docs/repo-aware-capture.md)
- Release operator validating outcome/security gates: [docs/evaluation-and-release-gates.md](docs/evaluation-and-release-gates.md)

### Installed CLI

The personal default is an installed Python package, private XDG storage,
SQLite, content-addressed filesystem blobs, and tokenless STDIO MCP:

```bash
pipx install research-registry
research-registry init
research-registry install-codex
research-registry doctor
```

Until a PyPI release, install from the repository:

```bash
uv tool install git+https://github.com/akovanda/Codex-research-skill
```

`init` applies packaged additive migrations and creates:

- `${XDG_CONFIG_HOME:-~/.config}/research-registry/config.toml`
- `${XDG_DATA_HOME:-~/.local/share}/research-registry/registry.sqlite3`
- `${XDG_DATA_HOME:-~/.local/share}/research-registry/blobs/`

Directories are mode `0700`; the config, SQLite database, backup artifacts, and
blob files are mode `0600` on POSIX. Personal config contains no API key,
admin token, or session secret. Re-running `init` verifies the same config and
schema without replacing user configuration.

Verify:

```bash
research-registry doctor
```

The plugin launches `research-registry mcp` as a child process. It binds no
network port, trusts the current operating-system user boundary, and needs no
daemon or token. See [Codex Plugin](docs/codex-plugin.md) for custom
`CODEX_HOME`, migration, uninstall, and security behavior.

### Source Checkout

If you are contributing from this repository, use:

```bash
make init
```

Verify the personal registry:

```bash
make doctor
```

What success looks like:

- `research-registry init` reports `status=current` on the second run
- `research-registry doctor` or `make doctor` reports `ok=true`
- the database, blob storage, migrations, STDIO wiring, and backup prerequisites
  are healthy
- after `research-registry install-codex`, the focused plugin is present under
  the configured `CODEX_HOME`

What `make init` does:

- creates `.venv/` if needed
- installs the project in editable mode
- initializes private XDG SQLite and blob storage
- applies additive v1 and v2 migrations

Manual equivalent:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
research-registry init
research-registry install-codex
```

Local default behavior:

- SQLite plus generated content-addressed filesystem blob paths
- tokenless, same-user STDIO MCP
- private and unreviewed research by default
- no background daemon and no Docker requirement
- explicit environment overrides still select a configured SQLite/Postgres
  database or remote shared backend

Create a verified online backup:

```bash
research-registry backup --output ./registry.backup.sqlite3
```

This writes a mode-`0600` SQLite backup, manifest, and personal config copy. The
manifest verifies database integrity and content hashes plus the referenced
blob inventory. Existing destinations are refused.

The optional loopback review server is an explicit foreground process. Supply
its admin credential through the environment, not a command-line argument:

```bash
RESEARCH_REGISTRY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  research-registry serve
```

The server uses HTTP authentication and is distinct from tokenless local STDIO.
Do not expose it directly to an untrusted network.

### Shared Postgres mode

The existing Compose/Postgres path remains supported for teams and existing
managed installations:

```bash
research-registry up
make shared-up
```

It is no longer the personal default. See [Compose deployment](docs/deploy-shared-compose.md)
and [managed Postgres migration choices](docs/migrate-managed-postgres.md).

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

Local research corpus settings:

- `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS`
- `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS_FILE`

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
5. embedded personal SQLite default

Local research root precedence:

1. explicit `source_roots` passed by code or CLI
2. `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS`
3. `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS_FILE`
4. current workspace root only

Example `RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS_FILE`:

```toml
paths = [
  "/path/to/repo-a",
  "/path/to/repo-b",
]

[roots]
frontend = "/path/to/frontend-monolith"
benchmarks = "/path/to/evals-repo"
```

Example env override:

```bash
export RESEARCH_REGISTRY_LOCAL_RESEARCH_ROOTS="/path/to/repo-a:/path/to/repo-b"
```

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
- `POST /api/follow-ups/{id}/status`
- `POST /api/sessions`
- `POST /api/sources`
- `POST /api/import/url`
- `POST /api/import/doi`
- `POST /api/import/bibtex`
- `POST /api/excerpts`
- `POST /api/claims`
- `POST /api/reports`
- `POST /api/reports/{id}/refresh`
- `POST /api/publish`

Workflow helpers:

- `POST /api/briefs/resolve`

Admin moderation:

- `POST /api/review`
- `POST /api/index-state`

Compatibility aliases:

- `/api/annotations/{id}` maps to excerpts
- `/api/findings/{id}` maps to claims

## Workflow Validation

Run the end-to-end workflow gate:

```bash
make workflow-check
```

This runs:

- the live `uvicorn` HTTP end-to-end test
- the memory/retrieval harness across all built-in scenarios using the current repo as the local source root
- the multi-domain harness across memory, inference, and eval topics using the current repo as the local source root

For the deeper built-in example suite:

```bash
make grounded-pass-check
```

This runs the 27-pass example research suite and writes a markdown report.

Artifacts:

- `.data/memory-retrieval-harness.sqlite3`
- `.data/domain-research-harness.sqlite3`
- `.data/research-pass-runner.md`

## MCP And Skills

Local STDIO MCP is the personal primary surface. The authenticated web app,
API, and HTTP MCP remain available for review/shared deployments:

- v2 Codex plugin: [`research-registry-plugin`](research-registry-plugin)
- focused read-only recall: [`research-recall`](research-registry-plugin/skills/research-recall/SKILL.md)
- explicit-only deposit: [`research-deposit`](research-registry-plugin/skills/research-deposit/SKILL.md)
- bundled local MCP launcher: `research-registry mcp --transport stdio`
- HTTP MCP endpoint: `http://127.0.0.1:8010/mcp/` after
  `research-registry up` or `make shared-up`
- stdio MCP server: `research-registry-mcp`
- legacy implicit capture skill: [`skills/research-capture`](skills/research-capture/SKILL.md)
- legacy memory/retrieval skill: [`skills/research-memory-retrieval`](skills/research-memory-retrieval/SKILL.md)
- checked-in repo profile example: [`.codex/repo-profile.toml`](.codex/repo-profile.toml)

The legacy skills are explicit-only and disabled unless
`RESEARCH_REGISTRY_LEGACY_HEURISTICS=1`. Low-level v1 MCP tools separately
require `RESEARCH_REGISTRY_MCP_LEGACY=1`. The default plugin loads neither
legacy surface; removal criteria are documented in
[Implicit research capture](docs/implicit-research-capture.md).

## Deployment

- [Getting started](docs/getting-started.md)
- [FAQ](docs/faq.md)
- [API quickstart](docs/api-quickstart.md)
- [Architecture](docs/architecture.md)
- [Local deployment](docs/deploy-local.md)
- [Shared Compose deployment](docs/deploy-shared-compose.md)
- [Existing managed Postgres migration](docs/migrate-managed-postgres.md)
- [Kubernetes deployment](docs/deploy-kubernetes.md)
- [Operations](docs/operations.md)
- [Codex plugin](docs/codex-plugin.md)
- [Implicit research capture](docs/implicit-research-capture.md)
- [Repo-aware capture](docs/repo-aware-capture.md)
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
make init
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
RESEARCH_REGISTRY_LEGACY_HEURISTICS=1 \
  ./.venv/bin/research-registry-pass-runner --db-path /tmp/research-pass-runner.sqlite3 --reset --rounds 2
```

## Preview Notes

- Personal SQLite/STDIO is the local default.
- Shared org mode is self-hosted, not multi-tenant cloud.
- Shared deployments are supported for internal-only exposure behind normal network controls.
- Local STDIO trusts the OS user boundary; shared HTTP uses API keys plus an
  admin token.
- Postgres is the intended backend for shared deployments.
