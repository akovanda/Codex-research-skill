# Getting Started

The default personal Research Registry is SQLite plus content-addressed
filesystem blobs, served to Codex over local STDIO. It does not require Docker,
Postgres, a daemon, a network port, or an API token.

## Prerequisites

- Python 3.12 or newer
- Codex on the same machine if you want the focused plugin
- `pipx`, `uv`, or `pip`

Docker is needed only for the separate shared Compose/Postgres deployment.

## Install and initialize

After a PyPI release:

```bash
pipx install research-registry
research-registry init
```

Until then:

```bash
uv tool install git+https://github.com/akovanda/Codex-research-skill
research-registry init
```

From a source checkout:

```bash
make init
```

The initializer creates:

- `${XDG_CONFIG_HOME:-~/.config}/research-registry/config.toml`
- `${XDG_DATA_HOME:-~/.local/share}/research-registry/registry.sqlite3`
- `${XDG_DATA_HOME:-~/.local/share}/research-registry/blobs/`

On POSIX, storage directories are mode `0700` and local files are mode `0600`.
The personal config contains storage metadata only—no API key, admin token, or
session secret. First run applies every packaged additive migration. Re-running
the command verifies the existing config and returns `status=current`; it never
silently replaces an unknown or shared config.

## Install the Codex plugin

Preview and apply the focused plugin installation:

```bash
research-registry install-codex --dry-run
research-registry install-codex
research-registry doctor
```

`install-codex` also initializes the personal registry when no explicit or
shared backend is configured. The plugin bundles:

- implicit, read-only `research-recall`
- explicit-only `research-deposit`
- `research-registry mcp` over STDIO

The MCP child process starts on demand, applies any pending additive migrations,
and immediately supports `research_status` and `research_search`. It trusts the
current OS user boundary, sends no telemetry, binds no network port, and needs
no token. Restart existing Codex conversations so they discover the plugin.

## Verify and back up

```bash
research-registry init --json
research-registry doctor
research-registry backup --output ./registry.backup.sqlite3
```

Healthy doctor output begins with `ok=true` and checks local config modes,
SQLite migrations/integrity, content-addressed blobs, tokenless STDIO wiring,
and online backup prerequisites without printing content or secrets.

Backup writes three new mode-`0600` files and refuses to overwrite:

- `registry.backup.sqlite3`
- `registry.backup.sqlite3.manifest.json`
- `registry.backup.sqlite3.config.toml`

The SQLite online backup API includes committed WAL state. The manifest records
database hashes and integrity, deterministic table inventory, the config
artifact hash, and the referenced blob inventory.

## Optional review server

Personal MCP needs no daemon. If you want the web review UI, run it explicitly
in the foreground with an environment-only HTTP credential:

```bash
export RESEARCH_REGISTRY_ADMIN_TOKEN="<generate-a-strong-random-value>"
research-registry serve
```

The default bind is `127.0.0.1:8010`. `serve` refuses to start without an admin
token. HTTP auth remains separate from tokenless same-user STDIO. Do not expose
the review server directly to an untrusted network.

## Shared and existing Docker/Postgres installations

Shared/team deployments still use authenticated HTTP, Postgres, and optional
Compose:

```bash
research-registry up
make shared-up
```

Do not point a new SQLite registry at a Postgres dump or copy database files
between dialects. Existing managed data is retained in Postgres unless you
perform a reviewed application-level export/import. See
[Managed Postgres migration choices](migrate-managed-postgres.md).

## Common issues

An existing config is refused:

- RR2-011 never overwrites an unknown config.
- If it is an earlier managed Docker/Postgres config, keep using it as the
  shared backend or follow the migration guide.
- If it is user-owned, move it only after making and verifying your own backup.

Codex does not show the plugin:

- rerun `research-registry install-codex`
- check `CODEX_HOME`
- start a new Codex conversation

Python is older than 3.12:

- use `uv python install 3.12`
- install the package with that interpreter

## Next docs

- [Local deployment and storage](deploy-local.md)
- [Codex plugin](codex-plugin.md)
- [Shared deployment with Compose](deploy-shared-compose.md)
- [Operations and restore verification](operations.md)
