CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    slug TEXT NOT NULL,
    focus_json TEXT NOT NULL,
    parent_topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_slug_namespace
    ON topics (slug, namespace_kind, namespace_id);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
    prompt TEXT NOT NULL,
    normalized_prompt TEXT NOT NULL,
    focus_json TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
    generated_by_session_id TEXT,
    generation_reason TEXT,
    priority_score REAL NOT NULL DEFAULT 0,
    visibility TEXT NOT NULL,
    author_type TEXT NOT NULL,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    human_reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_questions_prompt_namespace
    ON questions (normalized_prompt, namespace_kind, namespace_id);

CREATE INDEX IF NOT EXISTS idx_questions_parent
    ON questions (parent_question_id, priority_score DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    source_signals_json TEXT NOT NULL,
    notes TEXT,
    visibility TEXT NOT NULL,
    author_type TEXT NOT NULL,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    ttl_days INTEGER NOT NULL DEFAULT 30,
    expires_at TEXT,
    freshness_state TEXT NOT NULL DEFAULT 'fresh',
    refresh_of_session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_question
    ON research_sessions (question_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at
    ON research_sessions (expires_at, freshness_state);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    locator TEXT NOT NULL,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    site_name TEXT,
    published_at TEXT,
    accessed_at TEXT,
    author TEXT,
    snippet TEXT,
    content_sha256 TEXT,
    snapshot_url TEXT,
    snapshot_required INTEGER NOT NULL DEFAULT 0,
    snapshot_present INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    visibility TEXT NOT NULL,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sources_locator_hash
    ON sources (locator, content_sha256);

CREATE TABLE IF NOT EXISTS excerpts (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
    topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
    focal_label TEXT NOT NULL,
    note TEXT NOT NULL,
    selector_json TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    confidence REAL NOT NULL,
    tags_json TEXT NOT NULL,
    visibility TEXT NOT NULL,
    author_type TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    human_reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_excerpts_source ON excerpts (source_id);
CREATE INDEX IF NOT EXISTS idx_excerpts_question ON excerpts (question_id);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
    topic_id TEXT REFERENCES topics(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    focal_label TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    visibility TEXT NOT NULL,
    author_type TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    human_reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_question ON claims (question_id, created_at DESC);

CREATE TABLE IF NOT EXISTS claim_excerpts (
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    excerpt_id TEXT NOT NULL REFERENCES excerpts(id) ON DELETE RESTRICT,
    rationale TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (claim_id, excerpt_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    focal_label TEXT NOT NULL,
    summary_md TEXT NOT NULL,
    report_kind TEXT NOT NULL DEFAULT 'guidance',
    guidance_json TEXT NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL,
    author_type TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    namespace_kind TEXT NOT NULL DEFAULT 'user',
    namespace_id TEXT NOT NULL DEFAULT 'local',
    actor_user_id TEXT,
    actor_org_id TEXT,
    api_key_id TEXT,
    public_namespace_slug TEXT,
    public_index_state TEXT NOT NULL DEFAULT 'private',
    dedupe_key TEXT UNIQUE,
    human_reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_question ON reports (question_id, created_at DESC);

CREATE TABLE IF NOT EXISTS report_claims (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    PRIMARY KEY (report_id, claim_id)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_memberships (
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    actor_user_id TEXT NOT NULL,
    actor_org_id TEXT,
    namespace_kind TEXT NOT NULL,
    namespace_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    kind TEXT,
    record_id TEXT,
    api_key_id TEXT,
    actor_user_id TEXT,
    actor_org_id TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
