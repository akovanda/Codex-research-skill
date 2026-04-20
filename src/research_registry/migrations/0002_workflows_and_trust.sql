ALTER TABLE questions ADD COLUMN follow_up_status TEXT NOT NULL DEFAULT 'open';

ALTER TABLE sources ADD COLUMN review_state TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE sources ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'low';
ALTER TABLE sources ADD COLUMN conflict_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE sources ADD COLUMN refresh_due_at TEXT;

ALTER TABLE excerpts ADD COLUMN review_state TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE excerpts ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'low';
ALTER TABLE excerpts ADD COLUMN conflict_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE excerpts ADD COLUMN refresh_due_at TEXT;

ALTER TABLE claims ADD COLUMN review_state TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE claims ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE claims ADD COLUMN conflict_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE claims ADD COLUMN refresh_due_at TEXT;

ALTER TABLE reports ADD COLUMN refresh_of_report_id TEXT;
ALTER TABLE reports ADD COLUMN review_state TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE reports ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE reports ADD COLUMN conflict_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE reports ADD COLUMN refresh_due_at TEXT;

CREATE INDEX IF NOT EXISTS idx_questions_follow_up_status
    ON questions (follow_up_status, priority_score DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sources_refresh_due_at
    ON sources (refresh_due_at);

CREATE INDEX IF NOT EXISTS idx_claims_refresh_due_at
    ON claims (refresh_due_at);

CREATE INDEX IF NOT EXISTS idx_reports_refresh_due_at
    ON reports (refresh_due_at);
