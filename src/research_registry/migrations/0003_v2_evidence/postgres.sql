CREATE INDEX idx_source_versions_source
    ON source_versions (source_id, retrieved_at DESC);
CREATE INDEX idx_source_versions_hash
    ON source_versions (content_sha256);
CREATE INDEX idx_evidence_spans_source_version
    ON evidence_spans (source_version_id);
CREATE INDEX idx_evidence_spans_question
    ON evidence_spans (question_id, created_at DESC);
CREATE INDEX idx_claim_revisions_claim
    ON claim_revisions (claim_id, revision_number DESC);
CREATE INDEX idx_claim_evidence_span
    ON claim_evidence (evidence_span_id);
CREATE INDEX idx_review_events_entity
    ON review_events (entity_kind, entity_id, created_at);
CREATE INDEX idx_refresh_queue_status
    ON refresh_queue (status, priority DESC, detected_at);
CREATE UNIQUE INDEX idx_refresh_queue_unresolved_unique
    ON refresh_queue (entity_kind, entity_id, reason)
    WHERE status IN ('pending', 'running');
CREATE INDEX idx_backfill_warnings_legacy
    ON migration_backfill_warnings (entity_kind, legacy_id);
CREATE INDEX idx_backfill_errors_legacy
    ON migration_backfill_errors (entity_kind, legacy_id);

ALTER TABLE content_objects
    ADD CONSTRAINT content_objects_sha256_hex
    CHECK (sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE source_versions
    ADD CONSTRAINT source_versions_sha256_hex
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE evidence_spans
    ADD CONSTRAINT evidence_spans_sha256_hex
    CHECK (quote_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE idempotency_keys
    ADD CONSTRAINT idempotency_keys_sha256_hex
    CHECK (request_sha256 ~ '^[0-9a-f]{64}$');
