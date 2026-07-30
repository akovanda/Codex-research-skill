CREATE TABLE content_objects (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE
        CHECK (length(sha256) = 64 AND sha256 = lower(sha256)),
    storage_backend TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT,
    byte_count INTEGER CHECK (byte_count IS NULL OR byte_count >= 0),
    compression TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE source_versions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    version_key TEXT NOT NULL,
    version_kind TEXT NOT NULL CHECK (
        version_kind IN (
            'web', 'doi', 'file', 'git_blob', 'pdf', 'api', 'note', 'migration'
        )
    ),
    retrieved_at TEXT NOT NULL,
    published_at TEXT,
    content_sha256 TEXT NOT NULL
        CHECK (
            length(content_sha256) = 64
            AND content_sha256 = lower(content_sha256)
        ),
    content_object_id TEXT REFERENCES content_objects(id) ON DELETE RESTRICT,
    media_type TEXT,
    byte_count INTEGER CHECK (byte_count IS NULL OR byte_count >= 0),
    parser_name TEXT,
    parser_version TEXT,
    canonical_locator TEXT NOT NULL,
    repository_locator TEXT,
    commit_sha TEXT,
    blob_sha TEXT,
    path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (source_id, version_key),
    CHECK (
        version_kind <> 'git_blob'
        OR (commit_sha IS NOT NULL AND blob_sha IS NOT NULL AND path IS NOT NULL)
    )
);

CREATE TABLE evidence_spans (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL
        REFERENCES source_versions(id) ON DELETE RESTRICT,
    topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
    question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
    session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
    quote_text TEXT NOT NULL,
    quote_sha256 TEXT NOT NULL
        CHECK (
            length(quote_sha256) = 64
            AND quote_sha256 = lower(quote_sha256)
        ),
    selector_type TEXT NOT NULL CHECK (
        selector_type IN (
            'text_quote', 'line_range', 'char_range', 'page_range',
            'json_pointer', 'dom_text', 'git_line_range'
        )
    ),
    selector_json TEXT NOT NULL,
    note TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    anchor_state TEXT NOT NULL CHECK (
        anchor_state IN ('resolved', 'relocated', 'stale', 'invalid', 'unverified')
    ),
    review_state TEXT NOT NULL CHECK (
        review_state IN ('unreviewed', 'reviewed', 'flagged')
    ),
    trust_tier TEXT NOT NULL CHECK (trust_tier IN ('low', 'medium', 'high')),
    created_by_model TEXT,
    created_at TEXT NOT NULL,
    last_resolved_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE claim_revisions (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'draft', 'partial', 'supported', 'contested',
            'rejected', 'superseded'
        )
    ),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    valid_from TEXT,
    valid_until TEXT,
    supersedes_revision_id TEXT
        REFERENCES claim_revisions(id) ON DELETE RESTRICT,
    created_by_model TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (claim_id, revision_number)
);

ALTER TABLE claims ADD COLUMN canonical_key TEXT;
ALTER TABLE claims ADD COLUMN current_revision_id TEXT
    REFERENCES claim_revisions(id) ON DELETE RESTRICT;
ALTER TABLE claims ADD COLUMN scope_json TEXT;
ALTER TABLE claims ADD COLUMN updated_at TEXT;

CREATE TABLE claim_evidence (
    claim_revision_id TEXT NOT NULL
        REFERENCES claim_revisions(id) ON DELETE CASCADE,
    evidence_span_id TEXT NOT NULL
        REFERENCES evidence_spans(id) ON DELETE RESTRICT,
    relationship TEXT NOT NULL CHECK (
        relationship IN ('supports', 'refutes', 'qualifies', 'contextualizes')
    ),
    rationale TEXT,
    weight REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
    review_state TEXT NOT NULL CHECK (
        review_state IN ('unreviewed', 'approved', 'rejected')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (claim_revision_id, evidence_span_id)
);

CREATE TABLE review_events (
    id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL CHECK (
        entity_kind IN ('claim_revision', 'evidence', 'source_version', 'report')
    ),
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN (
            'approve', 'contest', 'reject', 'supersede',
            'refresh_requested', 'refresh_resolved'
        )
    ),
    from_state TEXT,
    to_state TEXT,
    note TEXT,
    actor_type TEXT NOT NULL CHECK (
        actor_type IN ('human', 'agent', 'system', 'migration')
    ),
    actor_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE refresh_queue (
    id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL CHECK (
        entity_kind IN ('source', 'evidence', 'claim', 'report')
    ),
    entity_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (
        reason IN (
            'expired', 'source_changed', 'anchor_missing', 'conflict', 'manual'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'resolved', 'dismissed', 'failed')
    ),
    priority REAL NOT NULL CHECK (priority >= 0 AND priority <= 1),
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE idempotency_keys (
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
    PRIMARY KEY (namespace_id, operation, "key")
);

CREATE TABLE migration_backfill_progress (
    migration_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    last_legacy_id TEXT,
    last_related_id TEXT,
    processed_count INTEGER NOT NULL DEFAULT 0
        CHECK (processed_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0
        CHECK (warning_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0
        CHECK (error_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (migration_id, phase)
);

CREATE TABLE migration_backfill_warnings (
    id TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    legacy_id TEXT NOT NULL,
    code TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (migration_id, entity_kind, legacy_id, code)
);

CREATE TABLE migration_backfill_errors (
    id TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    legacy_id TEXT NOT NULL,
    code TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (migration_id, entity_kind, legacy_id, code)
);
