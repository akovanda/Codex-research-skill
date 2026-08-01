CREATE TABLE review_event_stream (
    stream_position INTEGER PRIMARY KEY
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

CREATE TRIGGER review_events_assign_stream_position
AFTER INSERT ON review_events
BEGIN
    INSERT INTO review_event_stream (stream_position, event_id)
    SELECT COALESCE(MAX(stream_position), 0) + 1, NEW.id
    FROM review_event_stream;
END;

CREATE TRIGGER review_event_stream_immutable_update
BEFORE UPDATE ON review_event_stream
BEGIN
    SELECT RAISE(ABORT, 'review event stream is append-only');
END;

CREATE TRIGGER review_event_stream_immutable_delete
BEFORE DELETE ON review_event_stream
BEGIN
    SELECT RAISE(ABORT, 'review event stream is append-only');
END;
