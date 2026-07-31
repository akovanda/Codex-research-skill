CREATE FUNCTION reject_v2_immutable_mutation() RETURNS trigger AS $body$
BEGIN
    RAISE EXCEPTION 'v2 immutable record cannot be changed';
END;
$body$ LANGUAGE plpgsql;

CREATE TRIGGER source_versions_immutable
BEFORE UPDATE OR DELETE ON source_versions
FOR EACH ROW EXECUTE FUNCTION reject_v2_immutable_mutation();

CREATE TRIGGER evidence_spans_immutable
BEFORE UPDATE OR DELETE ON evidence_spans
FOR EACH ROW EXECUTE FUNCTION reject_v2_immutable_mutation();

CREATE TRIGGER claim_revisions_immutable
BEFORE UPDATE OR DELETE ON claim_revisions
FOR EACH ROW EXECUTE FUNCTION reject_v2_immutable_mutation();

CREATE TRIGGER review_events_append_only
BEFORE UPDATE OR DELETE ON review_events
FOR EACH ROW EXECUTE FUNCTION reject_v2_immutable_mutation();
