# Operations

This document covers the release-supported operator tasks for the `v0.1.0` preview.

Supported operator target:

- localhost runtime for local Codex instances
- shared self-hosted Compose deployment on internal-only networks

This preview does not claim support for direct public-internet exposure.

## Pre-migration data audit

`audit-data` opens SQLite in URI read-only mode with SQLite query-only enabled,
or starts a read-only Postgres transaction. It does not initialize the service,
run migrations, or repair records.

Run the real private audit locally and keep its output out of the repository:

```bash
umask 077
research-registry audit-data \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --json-out ./private-v1-audit.json \
  --markdown-out ./private-v1-audit.md
```

The default reports contain counts, hashes, state distributions, and health
results only. They omit prompts, quotes, claim/report bodies, source bodies,
URL query strings, tokens, and sampled content. Stop before migration if the
report finds foreign-key corruption or records that cannot be represented
without content loss.

## Backup and verified restore

SQLite backup uses the online SQLite backup API, so it includes committed WAL
state rather than blindly copying a live database file. The destination and
manifest must not already exist.

```bash
research-registry backup \
  --database ./registry.sqlite3 \
  --output ./registry-v1.backup.sqlite3 \
  --manifest ./registry-v1.backup.manifest.json

research-registry restore \
  --backup ./registry-v1.backup.sqlite3 \
  --manifest ./registry-v1.backup.manifest.json \
  --destination ./registry-v1.restore-check.sqlite3 \
  --verify
```

The manifest records the database SHA-256, byte count, table row counts,
deterministic per-table content hashes, integrity result, and v1 blob/config
status. Restore verification repeats the hash, integrity, foreign-key, row-count,
and deterministic-content checks against a new destination.

Research Registry v1 has no configured content-addressed blob store. The
manifest records that fact rather than claiming to include blobs. Back up
operator configuration separately:

- `~/.config/research-registry/config.toml`
- `~/.config/research-registry/.env`
- `~/.codex/config.toml.research-registry.bak`

Postgres support in RR2-000 is a redacted plan, not an automatic dump executor:

```bash
research-registry backup \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --output ./registry-v1.dump \
  --restore-database "$DISPOSABLE_RESTORE_DATABASE_URL" \
  --manifest ./registry-v1.postgres-plan.json
```

The plan uses custom-format `pg_dump`, `pg_restore --list`, and a
single-transaction restore to an explicitly disposable database. Commands are
represented as argument arrays; usernames, passwords, and URL query strings are
redacted. Supply real credentials directly to a subprocess environment or argv
when executing the reviewed plan. Do not paste a reconstructed command through
`shell=True`, and record both client and server versions during the rehearsal.

## Upgrade

Shared Compose:

```bash
git pull
cp deploy/.env.example deploy/.env  # only if you have not already created deploy/.env
docker compose -f deploy/compose.yaml --env-file deploy/.env up --build -d
```

Managed localhost runtime:

```bash
make up SEED_DEMO=0
make status
```

The current container startup path runs migrations before serving traffic. Upgrades should still be treated as intentional operational events, not invisible background changes.

## Rollback

If an upgrade fails:

1. stop the new app
2. restore the previous image or checkout
3. restore the previous verified database backup if the schema or data is no longer usable
4. restart and verify `/readyz`

Managed localhost runtime rollback helpers:

- stop with `make down`
- remove the managed localhost integration with `make uninstall`
- restore the previous Codex config from backup with `./.venv/bin/research-registry-local-uninstall --restore-codex-backup`
- fully remove local config/data and Docker volumes with `make purge-local`

## Token Rotation

Admin token and session secret are operator-managed values.

When rotating:

1. issue replacement API keys
2. update clients or Codex MCP config to use the replacement key
3. revoke old keys
4. restart the app if you changed admin token or session secret env vars

For the managed localhost runtime, inspect the current admin token and API key with:

```bash
make token
```

## Verification

After upgrade or rollback, verify:

- `GET /healthz` returns `200`
- `GET /readyz` returns `200`
- an authenticated write succeeds
- a search returns the expected stored record
