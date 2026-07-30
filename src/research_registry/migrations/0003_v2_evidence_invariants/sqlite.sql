CREATE TRIGGER source_versions_immutable_update
BEFORE UPDATE ON source_versions
BEGIN
    SELECT RAISE(ABORT, 'source versions are immutable');
END;

CREATE TRIGGER source_versions_immutable_delete
BEFORE DELETE ON source_versions
BEGIN
    SELECT RAISE(ABORT, 'source versions are immutable');
END;

CREATE TRIGGER evidence_spans_immutable_update
BEFORE UPDATE ON evidence_spans
BEGIN
    SELECT RAISE(ABORT, 'evidence spans are immutable');
END;

CREATE TRIGGER evidence_spans_immutable_delete
BEFORE DELETE ON evidence_spans
BEGIN
    SELECT RAISE(ABORT, 'evidence spans are immutable');
END;

CREATE TRIGGER claim_revisions_immutable_update
BEFORE UPDATE ON claim_revisions
BEGIN
    SELECT RAISE(ABORT, 'claim revisions are immutable');
END;

CREATE TRIGGER claim_revisions_immutable_delete
BEFORE DELETE ON claim_revisions
BEGIN
    SELECT RAISE(ABORT, 'claim revisions are immutable');
END;

CREATE TRIGGER review_events_append_only_update
BEFORE UPDATE ON review_events
BEGIN
    SELECT RAISE(ABORT, 'review events are append-only');
END;

CREATE TRIGGER review_events_append_only_delete
BEFORE DELETE ON review_events
BEGIN
    SELECT RAISE(ABORT, 'review events are append-only');
END;
