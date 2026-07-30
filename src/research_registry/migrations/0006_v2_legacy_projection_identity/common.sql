CREATE TABLE legacy_projection_identity (
    legacy_kind TEXT NOT NULL CHECK (
        legacy_kind IN ('source', 'excerpt', 'claim', 'report')
    ),
    legacy_id TEXT NOT NULL,
    v2_kind TEXT NOT NULL CHECK (
        v2_kind IN ('source_version', 'evidence', 'claim_revision', 'report')
    ),
    v2_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (legacy_kind, legacy_id),
    UNIQUE (v2_kind, v2_id),
    CHECK (
        (legacy_kind = 'source' AND v2_kind = 'source_version')
        OR (legacy_kind = 'excerpt' AND v2_kind = 'evidence')
        OR (legacy_kind = 'claim' AND v2_kind = 'claim_revision')
        OR (legacy_kind = 'report' AND v2_kind = 'report')
    )
);

CREATE INDEX idx_legacy_projection_identity_target
    ON legacy_projection_identity (v2_kind, v2_id);
