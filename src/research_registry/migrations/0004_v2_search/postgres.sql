ALTER TABLE search_documents
ADD COLUMN search_vector tsvector
GENERATED ALWAYS AS (
    to_tsvector(
        'simple'::regconfig,
        COALESCE(title, '') || ' ' ||
        COALESCE(summary, '') || ' ' ||
        COALESCE(body, '') || ' ' ||
        COALESCE(locator, '') || ' ' ||
        COALESCE(repository, '') || ' ' ||
        COALESCE(path, '') || ' ' ||
        COALESCE(canonical_key, '') || ' ' ||
        COALESCE(topic_slug, '') || ' ' ||
        COALESCE(dedupe_key, '')
    )
) STORED;

CREATE INDEX idx_search_documents_fts_gin
ON search_documents USING GIN (search_vector);
