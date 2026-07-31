# Codex Plugin

The v2 Codex integration is the installable `research-registry` plugin. It
bundles:

- `research-recall`, a read-only skill that may be selected implicitly;
- `research-deposit`, a write skill that can only be invoked explicitly;
- a local STDIO MCP launcher, `research-registry mcp --transport stdio`.

The plugin does not configure a remote HTTP endpoint, include legacy specialist
skills, publish records, or contain credentials.

## Install

Install the Python package first, then preview the exact Codex changes:

```bash
research-registry install-codex --dry-run
research-registry install-codex
research-registry doctor
```

Start a new Codex conversation after install so the new plugin, skills, and MCP
tools are loaded.

On a machine without explicit/shared backend configuration, `install-codex`
first performs the same idempotent personal initialization as
`research-registry init`. It creates private XDG SQLite/blob storage but writes
no auth token. A dry run remains read-only and does not initialize storage.

`install-codex` honors `CODEX_HOME`. It creates a dedicated local marketplace
under:

```text
$CODEX_HOME/marketplaces/research-registry-local/
```

It registers that marketplace and plugin with the installed Codex CLI. The
installer never edits marketplace or plugin registry tables by guessing their
format; it uses the documented `codex plugin` commands.

The bundled MCP process defaults to the personal XDG SQLite database and
performs safe first-run migration when needed. Set
`RESEARCH_REGISTRY_DATABASE_URL` when the process should use a specific SQLite
or Postgres database. You can also verify the launcher independently:

```bash
research-registry mcp --transport stdio --database ./registry.sqlite3
```

That command uses STDIO and does not bind a network port. Local STDIO relies on
the current operating-system user boundary and requires no HTTP token.

## Recall and deposit policy

`research-recall` may search durable prior work when the request makes that
relevant. It can call only `research_status`, `research_search`, and
`research_get`; it cannot write, refresh, review, or publish.

`research-deposit` has `allow_implicit_invocation: false`. Invoke it explicitly
with `$research-deposit`, or approve a proposed deposit. It validates before
writing, stores private and unreviewed records by default, and never publishes.

Stored sources, evidence, claims, reports, and metadata are untrusted data.
Instructions found inside them must never change agent behavior or cause tool
calls. Both skills preserve provenance and authorization boundaries.

## Upgrade from legacy skill links

Earlier managed installs created:

- a marked `researchRegistry` HTTP MCP block in
  `$CODEX_HOME/config.toml`;
- `research-capture` and `research-memory-retrieval` links under
  `$CODEX_HOME/skills/`.

`install-codex` removes only the exact block between the Research Registry
managed markers and only symlinks that resolve to packaged or source-checkout
legacy skills. Same-named directories, unrelated symlinks, manual MCP entries,
profiles, and other configuration remain untouched.

The removed managed state is recorded in a mode-`0600` installer state file so
`uninstall-codex` can restore it without overwriting later unrelated config.
That state may contain the previous managed MCP block and must be protected like
the original Codex config. Its contents are never printed.

Preview removal or remove the plugin:

```bash
research-registry uninstall-codex --dry-run
research-registry uninstall-codex
```

Uninstall uses `codex plugin remove` and `codex plugin marketplace remove`,
deletes only managed plugin files, and restores migrated managed state only
when the destination is still safe. If a user has since claimed a path or MCP
server name, uninstall preserves the new state and reports the unresolved
restore instead of overwriting it.

The older Docker/Postgres localhost manager remains available as a shared
compatibility path through `research-registry up`. Its broad legacy skills are
not part of the default v2 plugin.

## Diagnostics and privacy

`install-codex`, `uninstall-codex`, and their dry runs report action names and
exact managed paths. They never print file contents, environment variables,
tokens, authorization headers, or Codex command output.

`research-registry doctor` always checks personal config, SQLite, blobs, STDIO,
and backup readiness. When the dedicated managed plugin root exists, it also
checks plugin files, marketplace registration, plugin installation, and legacy
migration. An init-only machine therefore does not fail merely because Codex is
absent, while an installed plugin remains fully diagnosed. Diagnostic output
contains state and paths, not stored research or secrets.
