# Upgrade, Backup, Restore, and Rollback

The supported migration is additive. V1 tables and data remain present. Schema
rollback is not attempted in place; rollback restores a verified pre-upgrade
backup to a new database path and starts the prior application against that
restored copy.

## Automated copied-data rehearsal

```bash
make rr2-rehearsal-check
```

This creates disposable files only. It rehearses a fresh SQLite initialization,
a v1 database with a known record, verified online backup, additive schema
upgrade, resumable v2 backfill, row preservation, restore verification, and
rollback to the pre-upgrade schema. It requires zero data loss and zero
unresolved migration errors.

The unreleased `0005_v2_idempotency_namespace` migration adds
`namespace_kind` to the idempotency primary key. Existing v2-alpha rows did not
record that dimension and are deterministically backfilled as `user`
namespaces. Operators using pre-release organization deposits should inspect
those rows before upgrading; released v1 tables are neither renamed nor
deleted.

Wheel and sdist fresh installation, clean XDG paths, focused plugin installation,
and local STDIO status/search are covered by:

```bash
make rr2-package-check
```

## Personal SQLite upgrade

Use copies and explicit paths until the release gate is accepted:

1. Stop the local MCP/web processes.
2. Run `research-registry audit-data --database <database>`.
3. Run `research-registry backup --database <database> --output <backup>`.
4. Verify the generated backup manifest and retain the blob directory.
5. Run `research-registry migrate --database <database> --plan --json`.
6. Install the candidate wheel or sdist in a separate environment.
7. Run `research-registry migrate --database <database>`.
8. Run `research-registry migrate-v2-data --database <database> --resume`.
9. Run `research-registry migrate --database <database> --verify --json`.
10. Run `research-registry blob-health --database <database>`.
11. Start local STDIO MCP and smoke `research_status`, `research_search`, and
    `research_get`.

Do not overwrite the retained backup or its manifest.

## SQLite rollback

1. Stop the candidate application.
2. Select a new destination; never restore over the upgraded database.
3. Run:

   ```bash
   research-registry restore \
     --backup <pre-upgrade-backup> \
     --manifest <pre-upgrade-manifest> \
     --destination <new-rollback-database> \
     --verify
   ```

4. Restore the matching blob directory from host backup and verify its manifest.
5. Point the prior application version at the restored database.
6. Smoke v1 HTTP/MCP reads before returning traffic.

## Shared Postgres and Compose

Shared upgrade remains operator-run because it needs the real Postgres version,
credentials, durable blob mount, and deployment topology:

1. Audit and take a version-compatible `pg_dump`.
2. List/verify the dump and separately inventory the mounted blob directory.
3. Restore into a disposable Postgres database.
4. Run migration plan, migration, backfill, verification, and retrieval parity
   against the disposable restore.
5. Run `RUN_SHARED_COMPOSE_SMOKE=1 pytest -q
   tests/test_shared_compose_smoke.py`.
6. Only after the copied-data rehearsal passes, repeat during an approved
   maintenance window.

Rollback stops the candidate deployment, restores the retained database dump
and matching blob backup into new storage, runs integrity checks, and restarts
the prior image. Record the completed real-v1 and shared-Compose evidence in the
private operator-evidence file used by the beta/stable gate.

No command in this runbook tags, pushes, publishes, or automatically releases
an artifact.
