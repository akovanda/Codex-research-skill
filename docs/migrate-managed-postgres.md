# Existing Managed Docker/Postgres Data

RR2-011 changes the default for new personal installs. It does not move,
rewrite, delete, or silently adopt an existing managed Postgres database.
Compose, Postgres, v1 tables, HTTP contracts, and the earlier managed config
remain supported as the shared compatibility path.

## Choose before changing configuration

There are two safe choices.

### Keep the existing registry as shared

This is the default for an existing managed config:

```bash
research-registry audit-data \
  --database "$RESEARCH_REGISTRY_DATABASE_URL"
research-registry backup \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --output ./registry-before-rr2-011.dump \
  --manifest ./registry-before-rr2-011.plan.json
research-registry migrate \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --plan --json
```

Review and execute the redacted `pg_dump`/`pg_restore` plan using credentials
provided directly to the subprocess, verify the restore in a disposable
database, then apply and verify additive migrations:

```bash
research-registry migrate \
  --database "$RESEARCH_REGISTRY_DATABASE_URL"
research-registry migrate-v2-data \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --batch-size 500 --resume --json
research-registry migrate \
  --database "$RESEARCH_REGISTRY_DATABASE_URL" \
  --verify --json
```

Continue with `research-registry up` or the checked-in shared Compose
deployment. `install-codex` detects the existing shared managed config and does
not replace it with a personal config.

### Start an empty personal registry

Keep the verified Postgres backup and shared configuration, then initialize the
personal registry under a different explicit XDG root:

```bash
XDG_CONFIG_HOME="$PWD/personal-config" \
XDG_DATA_HOME="$PWD/personal-data" \
  research-registry init
```

Inspect both registries before changing the normal XDG paths. This creates a
new empty SQLite registry; it is not an import.

## Cross-dialect import boundary

A Postgres custom-format dump is not a SQLite database, and copying
`registry.sqlite3`, Postgres volume files, WAL files, or blob directories over
one another is unsupported.

RR2-011 does not add a lossless Postgres-to-SQLite export/import command.
Until a reviewed application-level exporter exists, keep the original
Postgres registry available as shared or transfer selected research through
the versioned v2 deposit contract with explicit idempotency keys. A selected
transfer must preserve:

- stable source identity and immutable source versions
- exact evidence text/selectors and source-version relationships
- claim revisions and typed evidence relationships
- report relationships
- namespace, visibility, review, conflict, and freshness state
- content hashes and referenced blob bytes

Do not rebuild those records with direct SQL or treat a search response as a
complete export. Search results are bounded retrieval views, not migration
artifacts.

## Stop conditions

Stop and restore the verified pre-change backup if:

- migration checksum verification fails
- Postgres contains unknown managed tables without schema history
- malformed legacy rows cannot be represented without loss
- referenced blobs are missing or corrupt
- a purported transfer changes private/public, review, or namespace state

No migration step should drop or rename a v1 table. Backfill is resumable and
idempotent; repeated migration verification should be a no-op.
