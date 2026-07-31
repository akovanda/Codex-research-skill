CREATE TABLE idempotency_keys_v2 (
    namespace_kind TEXT NOT NULL CHECK (namespace_kind IN ('user', 'org')),
    namespace_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    "key" TEXT NOT NULL,
    request_sha256 TEXT NOT NULL
        CHECK (
            length(request_sha256) = 64
            AND request_sha256 = lower(request_sha256)
        ),
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (namespace_kind, namespace_id, operation, "key")
);

INSERT INTO idempotency_keys_v2 (
    namespace_kind, namespace_id, operation, "key", request_sha256,
    response_json, created_at, expires_at
)
SELECT
    'user', namespace_id, operation, "key", request_sha256,
    response_json, created_at, expires_at
FROM idempotency_keys;

DROP TABLE idempotency_keys;
ALTER TABLE idempotency_keys_v2 RENAME TO idempotency_keys;

CREATE TRIGGER validate_idempotency_keys_sha256
BEFORE INSERT ON idempotency_keys
WHEN NEW.request_sha256 GLOB '*[^0-9a-f]*'
BEGIN
    SELECT RAISE(ABORT, 'invalid lowercase sha256');
END;

UPDATE search_documents
SET freshness = CASE
    WHEN (
        SELECT refresh_due_at FROM sources
        WHERE sources.id = search_documents.id
    ) IS NULL THEN 'unknown'
    WHEN (
        SELECT refresh_due_at FROM sources
        WHERE sources.id = search_documents.id
    ) <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now') THEN 'needs_refresh'
    ELSE 'fresh'
END
WHERE kind = 'source';
