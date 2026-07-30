CREATE VIRTUAL TABLE search_documents_fts USING fts5(
    title,
    summary,
    body,
    locator,
    repository,
    path,
    canonical_key,
    topic_slug,
    dedupe_key,
    content='search_documents',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER search_documents_fts_insert
AFTER INSERT ON search_documents
BEGIN
    INSERT INTO search_documents_fts (
        rowid, title, summary, body, locator, repository, path,
        canonical_key, topic_slug, dedupe_key
    ) VALUES (
        NEW.rowid, NEW.title, NEW.summary, NEW.body, NEW.locator,
        NEW.repository, NEW.path, NEW.canonical_key, NEW.topic_slug,
        NEW.dedupe_key
    );
END;

CREATE TRIGGER search_documents_fts_delete
AFTER DELETE ON search_documents
BEGIN
    INSERT INTO search_documents_fts (
        search_documents_fts, rowid, title, summary, body, locator,
        repository, path, canonical_key, topic_slug, dedupe_key
    ) VALUES (
        'delete', OLD.rowid, OLD.title, OLD.summary, OLD.body, OLD.locator,
        OLD.repository, OLD.path, OLD.canonical_key, OLD.topic_slug,
        OLD.dedupe_key
    );
END;

CREATE TRIGGER search_documents_fts_update
AFTER UPDATE ON search_documents
BEGIN
    INSERT INTO search_documents_fts (
        search_documents_fts, rowid, title, summary, body, locator,
        repository, path, canonical_key, topic_slug, dedupe_key
    ) VALUES (
        'delete', OLD.rowid, OLD.title, OLD.summary, OLD.body, OLD.locator,
        OLD.repository, OLD.path, OLD.canonical_key, OLD.topic_slug,
        OLD.dedupe_key
    );
    INSERT INTO search_documents_fts (
        rowid, title, summary, body, locator, repository, path,
        canonical_key, topic_slug, dedupe_key
    ) VALUES (
        NEW.rowid, NEW.title, NEW.summary, NEW.body, NEW.locator,
        NEW.repository, NEW.path, NEW.canonical_key, NEW.topic_slug,
        NEW.dedupe_key
    );
END;

INSERT INTO search_documents_fts(search_documents_fts) VALUES ('rebuild');
