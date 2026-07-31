# Personal Local Deployment

The personal default is a foreground, no-Docker installation:

- SQLite at the XDG data path
- generated content-addressed filesystem blob paths
- local STDIO MCP at the current OS-user boundary
- no daemon, system service, network listener, or stored auth token

Python 3.12+ is required. Linux is CI-covered; macOS is an intended preview
target. Windows is not yet claimed.

## Initialize

Installed package:

```bash
research-registry init
research-registry install-codex
research-registry doctor
```

Source checkout:

```bash
make init
./.venv/bin/research-registry install-codex
make doctor
```

`init` creates private config and data directories using `XDG_CONFIG_HOME` and
`XDG_DATA_HOME`, with standard `~/.config` and `~/.local/share` fallbacks.
The initializer's storage-root overrides are:

- `RESEARCH_REGISTRY_MANAGED_CONFIG_DIR`
- `RESEARCH_REGISTRY_MANAGED_DATA_DIR`

Runtime commands also continue to honor explicit database/backend overrides;
those overrides suppress automatic first-run initialization. Configured
personal database and blob paths must remain inside the active XDG data root.
Unknown, shared, or unsafe configs are refused rather than overwritten.

## Storage and modes

Default layout:

```text
~/.config/research-registry/
└── config.toml                  0600

~/.local/share/research-registry/
├── registry.sqlite3            0600
└── blobs/                      0700
    ├── .staging/               0700
    └── sha256/...
```

Directories are `0700`; SQLite, config, staged/final blobs, and backups are
`0600` on POSIX. SQLite foreign keys are enabled and personal initialization
uses WAL mode. Blob names are generated from SHA-256; callers cannot choose
filesystem paths.

First run applies the same packaged additive v1/v2 migrations used by Postgres.
Existing v1 tables and records remain. Re-running initialization verifies
checksums and is a no-op when current.

## STDIO MCP

The Codex plugin launches:

```bash
research-registry mcp --transport stdio
```

You can run it manually, but it is normally a child process managed by Codex.
The command performs safe first-run initialization when no backend override is
configured, then exposes status/search immediately. It does not bind a port or
require an API token. Private access is limited to the same local user and
local namespace.

## Doctor

```bash
research-registry doctor
```

Doctor is content-free and secret-free. It checks:

- personal config existence and modes
- SQLite migration/checksum state and file mode
- referenced content-addressed blob integrity
- packaged tokenless STDIO wiring
- online backup prerequisites

## Backup

```bash
research-registry backup --output ./registry.backup.sqlite3
```

The command refuses existing destinations and writes:

- an online SQLite backup including committed WAL state
- a manifest with hashes, integrity, deterministic row inventory, and blob
  references
- a verified personal config copy

Back up the referenced blob tree with your normal encrypted filesystem backup.
The manifest verifies every referenced blob against the live blob root before
accepting the database backup; it does not duplicate blob bodies beside every
database backup.

Restore to a new path and verify before use:

```bash
research-registry restore \
  --backup ./registry.backup.sqlite3 \
  --manifest ./registry.backup.sqlite3.manifest.json \
  --destination ./registry.restore-check.sqlite3 \
  --verify
```

Keep the config artifact and blob tree with the manifest.

## Optional web review server

The web UI is not required for MCP. Start it explicitly:

```bash
export RESEARCH_REGISTRY_ADMIN_TOKEN="<strong-random-value>"
research-registry serve
```

It binds `127.0.0.1:8010` by default and refuses to start without the
environment credential. HTTP MCP/API routes keep their normal authentication
rules and never inherit tokenless STDIO trust. Do not bind to a non-loopback
interface without normal TLS, trusted-host, network, and credential controls.

## Shared Compose remains separate

For a team or an existing managed Postgres installation:

```bash
research-registry up
make shared-up
```

That compatibility path creates Compose/runtime config and uses authenticated
HTTP. See [Shared Compose](deploy-shared-compose.md) and
[Managed Postgres migration choices](migrate-managed-postgres.md).
