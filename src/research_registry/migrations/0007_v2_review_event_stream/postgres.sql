CREATE TABLE review_event_stream (
    stream_position BIGINT PRIMARY KEY
        CHECK (stream_position >= 1),
    event_id TEXT NOT NULL UNIQUE
        REFERENCES review_events(id) ON DELETE RESTRICT
);

INSERT INTO review_event_stream (stream_position, event_id)
SELECT
    ROW_NUMBER() OVER (
        ORDER BY
            created_at ASC,
            CASE
                WHEN actor_type = 'migration'
                 AND action = 'contest'
                 AND to_state = 'conflicted'
                THEN 1 ELSE 0
            END ASC,
            id ASC
    ),
    id
FROM review_events
ORDER BY
    created_at ASC,
    CASE
        WHEN actor_type = 'migration'
         AND action = 'contest'
         AND to_state = 'conflicted'
        THEN 1 ELSE 0
    END ASC,
    id ASC;

CREATE FUNCTION append_review_event_stream_position() RETURNS trigger AS $body$
DECLARE
    next_position BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('research-registry:review-event-stream', 0)
    );
    SELECT COALESCE(MAX(stream_position), 0) + 1
      INTO next_position
      FROM review_event_stream;
    INSERT INTO review_event_stream (stream_position, event_id)
    VALUES (next_position, NEW.id);
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;

CREATE TRIGGER review_events_assign_stream_position
AFTER INSERT ON review_events
FOR EACH ROW EXECUTE FUNCTION append_review_event_stream_position();

CREATE TRIGGER review_event_stream_immutable
BEFORE UPDATE OR DELETE ON review_event_stream
FOR EACH ROW EXECUTE FUNCTION reject_v2_immutable_mutation();
