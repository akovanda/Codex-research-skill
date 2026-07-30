CREATE TABLE search_documents (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'question', 'source', 'source_version',
            'evidence', 'claim', 'report'
        )
    ),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT NOT NULL,
    locator TEXT,
    doi TEXT,
    repository TEXT,
    path TEXT,
    canonical_key TEXT,
    topic_slug TEXT,
    quote_hash TEXT,
    dedupe_key TEXT,
    review_state TEXT,
    trust_tier TEXT,
    conflict_state TEXT,
    freshness TEXT,
    status TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0
        CHECK (evidence_count >= 0),
    updated_at TEXT,
    created_at TEXT,
    url TEXT,
    source_type TEXT,
    topic_id TEXT,
    visibility TEXT NOT NULL,
    namespace_kind TEXT NOT NULL,
    namespace_id TEXT NOT NULL,
    public_index_state TEXT NOT NULL
);

CREATE INDEX idx_search_documents_kind
    ON search_documents (kind, updated_at DESC, id);
CREATE INDEX idx_search_documents_locator
    ON search_documents (locator);
CREATE INDEX idx_search_documents_doi
    ON search_documents (doi);
CREATE INDEX idx_search_documents_path
    ON search_documents (path);
CREATE INDEX idx_search_documents_canonical_key
    ON search_documents (canonical_key);
CREATE INDEX idx_search_documents_scope
    ON search_documents (namespace_kind, namespace_id, visibility, kind);

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    q.id, 'question', q.prompt, q.prompt,
    q.prompt || ' ' || q.normalized_prompt || ' ' || COALESCE(t.label, ''),
    NULL, NULL, NULL, NULL, NULL, t.slug, NULL, q.dedupe_key,
    CASE WHEN q.human_reviewed = 1 THEN 'reviewed' ELSE 'unreviewed' END,
    NULL, NULL,
    COALESCE((
        SELECT rs.freshness_state
        FROM research_sessions rs
        WHERE rs.question_id = q.id
        ORDER BY rs.created_at DESC
        LIMIT 1
    ), 'unknown'),
    q.status,
    (SELECT COUNT(*) FROM evidence_spans e WHERE e.question_id = q.id),
    q.created_at, q.created_at, NULL, NULL, q.topic_id, q.visibility,
    q.namespace_kind, q.namespace_id, q.public_index_state
FROM questions q
LEFT JOIN topics t ON t.id = q.topic_id;

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    s.id, 'source', s.title, COALESCE(s.snippet, s.locator),
    s.title || ' ' || s.locator || ' ' || COALESCE(s.snippet, ''),
    s.locator, NULL, NULL, NULL, NULL, NULL, NULL, s.dedupe_key,
    s.review_state, s.trust_tier, s.conflict_state,
    CASE WHEN s.refresh_due_at IS NOT NULL THEN 'needs_refresh' ELSE 'unknown' END,
    NULL,
    (SELECT COUNT(*)
     FROM evidence_spans e
     JOIN source_versions sv ON sv.id = e.source_version_id
     WHERE sv.source_id = s.id),
    COALESCE(s.last_verified_at, s.created_at), s.created_at, s.locator,
    s.source_type, NULL, s.visibility, s.namespace_kind, s.namespace_id,
    s.public_index_state
FROM sources s;

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    sv.id, 'source_version', s.title, sv.canonical_locator,
    s.title || ' ' || sv.canonical_locator || ' ' ||
        COALESCE(sv.repository_locator, '') || ' ' || COALESCE(sv.path, ''),
    sv.canonical_locator, NULL, sv.repository_locator, sv.path,
    NULL, NULL, NULL, NULL, s.review_state, s.trust_tier,
    s.conflict_state, 'unknown', NULL,
    (SELECT COUNT(*) FROM evidence_spans e WHERE e.source_version_id = sv.id),
    sv.retrieved_at, sv.created_at, sv.canonical_locator, s.source_type,
    NULL, s.visibility, s.namespace_kind, s.namespace_id,
    s.public_index_state
FROM source_versions sv
JOIN sources s ON s.id = sv.source_id;

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    e.id, 'evidence', s.title, e.quote_text,
    e.quote_text || ' ' || COALESCE(e.note, '') || ' ' || s.title || ' ' ||
        s.locator,
    s.locator, NULL, sv.repository_locator, sv.path, NULL, t.slug,
    e.quote_sha256, NULL, e.review_state, e.trust_tier, s.conflict_state,
    CASE
        WHEN e.anchor_state = 'stale' THEN 'stale'
        WHEN e.anchor_state IN ('resolved', 'relocated') THEN 'fresh'
        ELSE 'unknown'
    END,
    e.anchor_state, 1, e.created_at, e.created_at, s.locator, s.source_type,
    e.topic_id, s.visibility, s.namespace_kind, s.namespace_id,
    s.public_index_state
FROM evidence_spans e
JOIN source_versions sv ON sv.id = e.source_version_id
JOIN sources s ON s.id = sv.source_id
LEFT JOIN topics t ON t.id = e.topic_id;

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    c.id, 'claim', COALESCE(cr.title, c.title),
    COALESCE(cr.statement, c.statement),
    COALESCE(cr.title, c.title) || ' ' ||
        COALESCE(cr.statement, c.statement) || ' ' ||
        COALESCE(c.focal_label, '') || ' ' ||
        COALESCE(c.canonical_key, ''),
    NULL, NULL, NULL, NULL, c.canonical_key, t.slug, NULL, c.dedupe_key,
    c.review_state, c.trust_tier, c.conflict_state,
    COALESCE(rs.freshness_state, 'unknown'),
    COALESCE(cr.status, c.status),
    (SELECT COUNT(*)
     FROM claim_evidence ce
     WHERE ce.claim_revision_id = c.current_revision_id),
    COALESCE(c.updated_at, c.created_at), c.created_at, NULL, NULL,
    c.topic_id, c.visibility, c.namespace_kind, c.namespace_id,
    c.public_index_state
FROM claims c
LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
LEFT JOIN research_sessions rs ON rs.id = c.session_id
LEFT JOIN topics t ON t.id = c.topic_id;

INSERT INTO search_documents (
    id, kind, title, summary, body, locator, doi, repository, path,
    canonical_key, topic_slug, quote_hash, dedupe_key, review_state,
    trust_tier, conflict_state, freshness, status, evidence_count,
    updated_at, created_at, url, source_type, topic_id, visibility,
    namespace_kind, namespace_id, public_index_state
)
SELECT
    r.id, 'report', r.title, r.summary_md,
    r.title || ' ' || r.summary_md || ' ' || COALESCE(r.guidance_json, ''),
    NULL, NULL, NULL, NULL, NULL, t.slug, NULL, r.dedupe_key,
    r.review_state, r.trust_tier, r.conflict_state,
    COALESCE(rs.freshness_state, 'unknown'), NULL,
    (SELECT COUNT(*)
     FROM report_claims rc
     JOIN claims c ON c.id = rc.claim_id
     JOIN claim_evidence ce
       ON ce.claim_revision_id = c.current_revision_id
     WHERE rc.report_id = r.id),
    r.created_at, r.created_at, NULL, NULL, q.topic_id, r.visibility,
    r.namespace_kind, r.namespace_id, r.public_index_state
FROM reports r
LEFT JOIN research_sessions rs ON rs.id = r.session_id
LEFT JOIN questions q ON q.id = r.question_id
LEFT JOIN topics t ON t.id = q.topic_id;
