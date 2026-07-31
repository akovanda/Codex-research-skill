ALTER TABLE idempotency_keys
    ADD COLUMN namespace_kind TEXT;

UPDATE idempotency_keys
SET namespace_kind = 'user'
WHERE namespace_kind IS NULL;

ALTER TABLE idempotency_keys
    ALTER COLUMN namespace_kind SET NOT NULL;

ALTER TABLE idempotency_keys
    ADD CONSTRAINT idempotency_keys_namespace_kind
    CHECK (namespace_kind IN ('user', 'org'));

ALTER TABLE idempotency_keys
    DROP CONSTRAINT idempotency_keys_pkey;

ALTER TABLE idempotency_keys
    ADD PRIMARY KEY (namespace_kind, namespace_id, operation, "key");

UPDATE search_documents sd
SET freshness = CASE
    WHEN s.refresh_due_at IS NULL THEN 'unknown'
    WHEN s.refresh_due_at <=
         to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
                 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    THEN 'needs_refresh'
    ELSE 'fresh'
END
FROM sources s
WHERE sd.kind = 'source' AND sd.id = s.id;
