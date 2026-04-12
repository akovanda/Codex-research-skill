# Operations

This document covers the release-supported operator tasks for the `v0.1.0` preview.

Supported operator target:

- localhost runtime for local Codex instances
- shared self-hosted Compose deployment on internal-only networks

This preview does not claim support for direct public-internet exposure.

## Backup

For any deployment that matters, back up Postgres before upgrades.

Recommended minimum:

- dump the full database with `pg_dump`
- retain the current app image or checked-out commit
- retain the current runtime env file and admin token storage

Example:

```bash
pg_dump "$RESEARCH_REGISTRY_DATABASE_URL" > research-registry-backup.sql
```

For the managed localhost runtime, also keep a copy of:

- `~/.config/research-registry/config.toml`
- `~/.config/research-registry/.env`
- `~/.codex/config.toml.research-registry.bak`

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
3. restore the previous database backup if the schema or data is no longer usable
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
