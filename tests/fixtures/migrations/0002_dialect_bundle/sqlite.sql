CREATE INDEX fixture_bundle_dialect_idx
    ON fixture_bundle (dialect);

CREATE TRIGGER fixture_bundle_normalize_dialect
AFTER INSERT ON fixture_bundle
FOR EACH ROW
BEGIN
    UPDATE fixture_bundle
    SET dialect = LOWER(NEW.dialect)
    WHERE id = NEW.id;
END;
