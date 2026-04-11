from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

from .models import (
    ApiKeyCreate,
    ApiKeyRecord,
    AuthContext,
    BackendStatus,
    ClaimCreate,
    ClaimRecord,
    DashboardData,
    ExcerptCreate,
    ExcerptRecord,
    FocusTuple,
    GuidancePayload,
    IndexStateRequest,
    IssuedApiKey,
    OrganizationRecord,
    PublishRequest,
    QuestionCreate,
    QuestionRecord,
    QuestionStatus,
    RecordKind,
    ReportCreate,
    ReportRecord,
    ResearchSessionCreate,
    ResearchSessionRecord,
    ReviewRequest,
    SearchHit,
    SearchResponse,
    SourceCreate,
    SourceRecord,
    SourceSelector,
    TopicCreate,
    TopicRecord,
    UserRecord,
    Visibility,
)

SCHEMA_VERSION = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalize_prompt(text: str) -> str:
    return " ".join(text.strip().lower().split())


def first_non_heading_line(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return markdown.strip().splitlines()[0] if markdown.strip() else ""


class RegistryService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._backend_status = BackendStatus(
            name="embedded-local",
            kind="local",
            selection_source="embedded_local",
            url=None,
            namespace_kind="user",
            namespace_id="local",
            api_key_present=False,
        )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def set_backend_status(self, status: BackendStatus) -> None:
        self._backend_status = status

    def backend_status(self) -> BackendStatus:
        return self._backend_status

    def initialize(self) -> None:
        with self.connect() as conn:
            version = None
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            if "schema_meta" in tables:
                row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
                version = row["version"] if row else None
            if version is None:
                self._create_schema(conn)
            elif version < SCHEMA_VERSION:
                self._migrate_schema(conn, version)
                self._create_schema(conn)
            elif version == SCHEMA_VERSION:
                self._create_schema(conn)
            else:
                self._drop_managed_schema(conn)
                self._create_schema(conn)

    def create_topic(self, payload: TopicCreate, auth: AuthContext | None = None) -> TopicRecord:
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "topics", payload.dedupe_key)
            if existing:
                return self._topic_from_row(existing)
            existing = conn.execute(
                """
                SELECT * FROM topics
                WHERE slug = ? AND namespace_kind = ? AND namespace_id = ?
                LIMIT 1
                """,
                (payload.slug, metadata["namespace_kind"], metadata["namespace_id"]),
            ).fetchone()
            if existing:
                return self._topic_from_row(existing)
            topic_id = self._new_id("topic")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO topics (
                    id, label, slug, focus_json, parent_topic_id,
                    namespace_kind, namespace_id, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    payload.label,
                    payload.slug,
                    payload.focus.model_dump_json(),
                    payload.parent_topic_id,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    payload.dedupe_key,
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return self._topic_from_row(row)

    def get_topic(self, topic_id: str) -> TopicRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"topic:{topic_id} not found")
        return self._topic_from_row(row)

    def create_question(self, payload: QuestionCreate, auth: AuthContext | None = None) -> QuestionRecord:
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        topic_id = payload.topic_id
        if topic_id is None:
            assert payload.focus is not None
            topic = self.create_topic(
                TopicCreate(
                    focus=payload.focus,
                    label=payload.focus.label,
                    slug=payload.focus.slug,
                    namespace_kind=metadata["namespace_kind"],
                    namespace_id=metadata["namespace_id"],
                    dedupe_key=f"topic:{metadata['namespace_kind']}:{metadata['namespace_id']}:{payload.focus.slug}",
                ),
                auth=auth,
            )
            topic_id = topic.id
        else:
            topic = self.get_topic(topic_id)

        normalized_prompt = normalize_prompt(payload.prompt)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "questions", payload.dedupe_key)
            if existing:
                return self._question_from_row(existing)
            existing = conn.execute(
                """
                SELECT * FROM questions
                WHERE normalized_prompt = ? AND topic_id = ? AND namespace_kind = ? AND namespace_id = ?
                LIMIT 1
                """,
                (normalized_prompt, topic_id, metadata["namespace_kind"], metadata["namespace_id"]),
            ).fetchone()
            if existing:
                return self._question_from_row(existing)
            question_id = self._new_id("q")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO questions (
                    id, topic_id, prompt, normalized_prompt, focus_json, status,
                    parent_question_id, generated_by_session_id, generation_reason, priority_score,
                    visibility, author_type, namespace_kind, namespace_id, actor_user_id,
                    actor_org_id, api_key_id, public_namespace_slug, public_index_state,
                    dedupe_key, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    topic_id,
                    payload.prompt,
                    normalized_prompt,
                    topic.focus.model_dump_json(),
                    payload.status,
                    payload.parent_question_id,
                    payload.generated_by_session_id,
                    payload.generation_reason,
                    payload.priority_score,
                    payload.visibility,
                    payload.author_type,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    0,
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        return self._question_from_row(row)

    def get_question(
        self,
        question_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> QuestionRecord:
        row = self._fetch_row("questions", question_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._question_from_row(row)

    def set_question_status(self, question_id: str, status: QuestionStatus) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE questions SET status = ? WHERE id = ?", (status, question_id))

    def create_session(self, payload: ResearchSessionCreate, auth: AuthContext | None = None) -> ResearchSessionRecord:
        question = self.get_question(payload.question_id, include_private=True)
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "research_sessions", payload.dedupe_key)
            if existing:
                return self._session_from_row(existing)
            session_id = self._new_id("sess")
            created_at = utc_now()
            expires_at = created_at + timedelta(days=payload.ttl_days)
            status = "insufficient_evidence" if payload.mode == "insufficient_evidence" else "completed"
            conn.execute(
                """
                INSERT INTO research_sessions (
                    id, question_id, prompt, model_name, model_version, mode, status,
                    source_signals_json, notes, visibility, author_type, namespace_kind,
                    namespace_id, actor_user_id, actor_org_id, api_key_id, public_namespace_slug,
                    public_index_state, dedupe_key, ttl_days, expires_at, freshness_state,
                    refresh_of_session_id, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    question.id,
                    payload.prompt or question.prompt,
                    payload.model_name,
                    payload.model_version,
                    payload.mode,
                    status,
                    json.dumps(payload.source_signals),
                    payload.notes,
                    payload.visibility,
                    payload.author_type,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    payload.ttl_days,
                    expires_at.isoformat(),
                    "fresh",
                    payload.refresh_of_session_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM research_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session_from_row(row)

    def get_session(
        self,
        session_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> ResearchSessionRecord:
        row = self._fetch_row("research_sessions", session_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._session_from_row(row)

    def create_source(self, payload: SourceCreate, auth: AuthContext | None = None) -> SourceRecord:
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "sources", payload.dedupe_key)
            if existing:
                return self._source_from_row(existing)
            existing = conn.execute(
                """
                SELECT * FROM sources
                WHERE locator = ? AND COALESCE(content_sha256, '') = COALESCE(?, '')
                  AND namespace_kind = ? AND namespace_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (payload.locator, payload.content_sha256, metadata["namespace_kind"], metadata["namespace_id"]),
            ).fetchone()
            if existing:
                return self._source_from_row(existing)
            source_id = self._new_id("src")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO sources (
                    id, locator, title, source_type, site_name, published_at, accessed_at,
                    author, snippet, content_sha256, snapshot_url, snapshot_required,
                    snapshot_present, last_verified_at, visibility, namespace_kind, namespace_id,
                    actor_user_id, actor_org_id, api_key_id, public_namespace_slug,
                    public_index_state, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    payload.locator,
                    payload.title,
                    payload.source_type,
                    payload.site_name,
                    self._encode_dt(payload.published_at),
                    self._encode_dt(payload.accessed_at),
                    payload.author,
                    payload.snippet,
                    payload.content_sha256,
                    payload.snapshot_url,
                    int(payload.snapshot_required),
                    int(payload.snapshot_present),
                    self._encode_dt(payload.last_verified_at),
                    payload.visibility,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_from_row(row)

    def get_source(
        self,
        source_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> SourceRecord:
        row = self._fetch_row("sources", source_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._source_from_row(row)

    def create_excerpt(self, payload: ExcerptCreate, auth: AuthContext | None = None) -> ExcerptRecord:
        question = self.get_question(payload.question_id, include_private=True)
        source = self._resolve_source(payload, auth=auth)
        topic_id = payload.topic_id or question.topic_id
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "excerpts", payload.dedupe_key)
            if existing:
                return self._excerpt_from_row(existing)
            excerpt_id = self._new_id("ex")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO excerpts (
                    id, source_id, question_id, session_id, topic_id, focal_label, note,
                    selector_json, quote_text, confidence, tags_json, visibility,
                    author_type, model_name, model_version, namespace_kind, namespace_id,
                    actor_user_id, actor_org_id, api_key_id, public_namespace_slug,
                    public_index_state, dedupe_key, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    excerpt_id,
                    source.id,
                    question.id,
                    payload.session_id,
                    topic_id,
                    payload.focal_label,
                    payload.note,
                    payload.selector.model_dump_json(),
                    payload.quote_text,
                    payload.confidence,
                    json.dumps(payload.tags),
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    0,
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM excerpts WHERE id = ?", (excerpt_id,)).fetchone()
        return self._excerpt_from_row(row)

    def get_excerpt(
        self,
        excerpt_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> ExcerptRecord:
        row = self._fetch_row("excerpts", excerpt_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._excerpt_from_row(row)

    def create_claim(self, payload: ClaimCreate, auth: AuthContext | None = None) -> ClaimRecord:
        question = self.get_question(payload.question_id, include_private=True)
        excerpt_ids = []
        for excerpt_id in payload.excerpt_ids:
            excerpt = self.get_excerpt(excerpt_id, include_private=True)
            excerpt_ids.append(excerpt.id)
        topic_id = payload.topic_id or question.topic_id
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "claims", payload.dedupe_key)
            if existing:
                return self._claim_from_row(existing)
            claim_id = self._new_id("clm")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO claims (
                    id, question_id, session_id, topic_id, title, focal_label,
                    statement, status, confidence, visibility, author_type,
                    model_name, model_version, namespace_kind, namespace_id,
                    actor_user_id, actor_org_id, api_key_id, public_namespace_slug,
                    public_index_state, dedupe_key, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    question.id,
                    payload.session_id,
                    topic_id,
                    payload.title,
                    payload.focal_label,
                    payload.statement,
                    payload.status,
                    payload.confidence,
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    0,
                    created_at.isoformat(),
                ),
            )
            for excerpt_id in excerpt_ids:
                conn.execute(
                    """
                    INSERT INTO claim_excerpts (claim_id, excerpt_id, rationale, weight)
                    VALUES (?, ?, ?, ?)
                    """,
                    (claim_id, excerpt_id, None, 1.0),
                )
            row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return self._claim_from_row(row)

    def get_claim(
        self,
        claim_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> ClaimRecord:
        row = self._fetch_row("claims", claim_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._claim_from_row(row)

    def create_report(self, payload: ReportCreate, auth: AuthContext | None = None) -> ReportRecord:
        question = self.get_question(payload.question_id, include_private=True)
        claim_ids = [self.get_claim(claim_id, include_private=True).id for claim_id in payload.claim_ids]
        metadata = self._write_metadata(payload.namespace_kind, payload.namespace_id, auth)
        with self.connect() as conn:
            existing = self._fetch_existing_by_dedupe_key(conn, "reports", payload.dedupe_key)
            if existing:
                return self._report_from_row(existing)
            report_id = self._new_id("rpt")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO reports (
                    id, question_id, session_id, title, focal_label, summary_md,
                    report_kind, guidance_json, visibility, author_type, model_name,
                    model_version, namespace_kind, namespace_id, actor_user_id, actor_org_id,
                    api_key_id, public_namespace_slug, public_index_state, dedupe_key,
                    human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    question.id,
                    payload.session_id,
                    payload.title,
                    payload.focal_label,
                    payload.summary_md,
                    payload.report_kind,
                    payload.guidance.model_dump_json(),
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    metadata["namespace_kind"],
                    metadata["namespace_id"],
                    metadata["actor_user_id"],
                    metadata["actor_org_id"],
                    metadata["api_key_id"],
                    metadata["public_namespace_slug"],
                    self._public_index_state_for_visibility(payload.visibility),
                    payload.dedupe_key,
                    0,
                    created_at.isoformat(),
                ),
            )
            for claim_id in claim_ids:
                conn.execute(
                    "INSERT INTO report_claims (report_id, claim_id) VALUES (?, ?)",
                    (report_id, claim_id),
                )
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        self.set_question_status(question.id, "answered")
        return self._report_from_row(row)

    def get_report(
        self,
        report_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> ReportRecord:
        row = self._fetch_row("reports", report_id)
        self._ensure_visible(row, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return self._report_from_row(row)

    def list_claims_for_question(
        self,
        question_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[ClaimRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM claims WHERE question_id = ? ORDER BY created_at DESC",
                (question_id,),
            ).fetchall()
        return [
            self._claim_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]

    def list_reports_for_question(
        self,
        question_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[ReportRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports WHERE question_id = ? ORDER BY created_at DESC",
                (question_id,),
            ).fetchall()
        return [
            self._report_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]

    def list_sessions_for_question(
        self,
        question_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[ResearchSessionRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_sessions WHERE question_id = ? ORDER BY created_at DESC",
                (question_id,),
            ).fetchall()
        records = [
            self._session_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]
        return sorted(records, key=lambda session: (not session.is_stale, session.created_at), reverse=True)

    def list_child_questions(
        self,
        question_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[QuestionRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM questions WHERE parent_question_id = ? ORDER BY priority_score DESC, created_at DESC",
                (question_id,),
            ).fetchall()
        return [
            self._question_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]

    def list_excerpts_for_claim(
        self,
        claim_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[ExcerptRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*
                FROM excerpts e
                JOIN claim_excerpts ce ON ce.excerpt_id = e.id
                WHERE ce.claim_id = ?
                ORDER BY e.created_at ASC
                """,
                (claim_id,),
            ).fetchall()
        return [
            self._excerpt_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]

    def list_excerpts_for_source(
        self,
        source_id: str,
        *,
        include_private: bool = False,
        auth: AuthContext | None = None,
    ) -> list[ExcerptRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM excerpts WHERE source_id = ? ORDER BY created_at DESC",
                (source_id,),
            ).fetchall()
        return [
            self._excerpt_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=False, namespace_slug=None)
        ]

    def search(
        self,
        query: str,
        *,
        kind: RecordKind | None = None,
        include_private: bool = False,
        limit: int = 20,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> SearchResponse:
        canonical_kind = self._canonical_kind(kind)
        normalized = normalize_prompt(query)
        hits: list[SearchHit] = []
        if canonical_kind in (None, "question"):
            hits.extend(self._search_questions(normalized, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug))
        if canonical_kind in (None, "claim"):
            hits.extend(self._search_claims(normalized, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug))
        if canonical_kind in (None, "report"):
            hits.extend(self._search_reports(normalized, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug))
        if canonical_kind in (None, "source"):
            hits.extend(self._search_sources(normalized, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug))
        if canonical_kind in (None, "excerpt"):
            hits.extend(self._search_excerpts(normalized, include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug))
        hits.sort(key=lambda item: (item.score, item.created_at), reverse=True)
        return SearchResponse(query=query, hits=hits[:limit])

    def dashboard(
        self,
        *,
        include_private: bool,
        limit: int = 8,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> DashboardData:
        reports = self._list_reports(include_private=include_private, limit=limit, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        claims = self._list_claims(include_private=include_private, limit=limit, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        questions = self._list_questions(include_private=include_private, limit=limit, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        return DashboardData(reports=reports, claims=claims, questions=questions)

    def publish(self, payload: PublishRequest, auth: AuthContext | None = None) -> None:
        if auth is not None and not auth.has_scope("publish"):
            raise PermissionError("publish scope required")
        canonical_kind = self._canonical_kind(payload.kind)
        with self.connect() as conn:
            self._set_visibility(conn, canonical_kind, payload.record_id, "public", include_in_global_index=payload.include_in_global_index)
            if payload.cascade_linked_sources:
                self._cascade_publish(conn, canonical_kind, payload.record_id, include_in_global_index=payload.include_in_global_index)
            self._record_audit(
                conn,
                action="publish",
                kind=canonical_kind,
                record_id=payload.record_id,
                auth=auth,
                details={"include_in_global_index": payload.include_in_global_index},
            )

    def review(self, payload: ReviewRequest, auth: AuthContext | None = None) -> None:
        canonical_kind = self._canonical_kind(payload.kind)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE {self._table_name(canonical_kind)} SET human_reviewed = ? WHERE id = ?",
                (int(payload.reviewed), payload.record_id),
            )
            self._record_audit(
                conn,
                action="review",
                kind=canonical_kind,
                record_id=payload.record_id,
                auth=auth,
                details={"reviewed": payload.reviewed},
            )

    def set_index_state(self, payload: IndexStateRequest, auth: AuthContext | None = None) -> None:
        if auth is not None and not auth.is_admin:
            raise PermissionError("admin scope required")
        canonical_kind = self._canonical_kind(payload.kind)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE {self._table_name(canonical_kind)} SET public_index_state = ? WHERE id = ?",
                (payload.state, payload.record_id),
            )
            self._record_audit(
                conn,
                action="set_index_state",
                kind=canonical_kind,
                record_id=payload.record_id,
                auth=auth,
                details={"state": payload.state},
            )

    def seed_demo(self) -> dict[str, str]:
        if self.search("zebra", include_private=True).hits:
            return {}
        focus = FocusTuple(domain="biology", object="zebra coat pattern", concern="wild-function evidence")
        question = self.create_question(QuestionCreate(prompt="What do zebra coat patterns do in the wild?", focus=focus))
        session = self.create_session(
            ResearchSessionCreate(
                question_id=question.id,
                prompt=question.prompt,
                model_name="gpt-5.4",
                model_version="2026-04-10",
                mode="live_research",
                notes="demo seed",
            )
        )
        source = self.create_source(
            SourceCreate(
                locator="https://example.org/zebra-striped-passage",
                title="Field observations on zebra striping",
                source_type="paper",
                site_name="Example Ecology",
                author="A. Researcher",
                snippet="Zebra stripes may reduce biting fly landings and support social recognition.",
                content_sha256=self._hash_text("field observations zebra stripes"),
                snapshot_required=True,
                snapshot_present=True,
            )
        )
        excerpt = self.create_excerpt(
            ExcerptCreate(
                source_id=source.id,
                question_id=question.id,
                session_id=session.id,
                focal_label=focus.label or "zebra coat pattern",
                note="This passage directly supports the fly-avoidance explanation.",
                selector=SourceSelector(
                    exact="Zebra stripes may reduce biting fly landings and support social recognition.",
                    deep_link="https://example.org/zebra-striped-passage#p2",
                    start_line=12,
                    end_line=12,
                ),
                quote_text="Zebra stripes may reduce biting fly landings and support social recognition.",
                tags=["zebra", "flies", "field-study"],
            )
        )
        claim = self.create_claim(
            ClaimCreate(
                question_id=question.id,
                session_id=session.id,
                title="Fly avoidance is directly evidenced",
                focal_label=focus.label or "zebra coat pattern",
                statement="The strongest evidence in this demo corpus supports the claim that zebra stripes reduce fly landings.",
                excerpt_ids=[excerpt.id],
                confidence=0.82,
            )
        )
        report = self.create_report(
            ReportCreate(
                question_id=question.id,
                session_id=session.id,
                title=question.prompt,
                focal_label=focus.label or "zebra coat pattern",
                summary_md=(
                    f"# {question.prompt}\n\n"
                    "## Direct Answer\n"
                    "The seeded evidence supports the fly-avoidance explanation.\n\n"
                    "## Claims\n"
                    f"1. {claim.statement}\n\n"
                    "## Evidence\n"
                    f"- {source.title}: {source.locator}\n"
                ),
                claim_ids=[claim.id],
            )
        )
        self.review(ReviewRequest(kind="claim", record_id=claim.id))
        self.publish(PublishRequest(kind="report", record_id=report.id, include_in_global_index=True))
        return {"question_id": question.id, "report_id": report.id}

    def ensure_user(self, user_id: str, display_name: str | None = None) -> UserRecord:
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, display_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET display_name = COALESCE(users.display_name, excluded.display_name)
                """,
                (user_id, display_name or user_id, created_at.isoformat()),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return UserRecord(id=row["id"], display_name=row["display_name"], created_at=datetime.fromisoformat(row["created_at"]))

    def ensure_organization(self, org_id: str, display_name: str | None = None) -> OrganizationRecord:
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO organizations (id, display_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET display_name = COALESCE(organizations.display_name, excluded.display_name)
                """,
                (org_id, display_name or org_id, created_at.isoformat()),
            )
            row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        return OrganizationRecord(id=row["id"], display_name=row["display_name"], created_at=datetime.fromisoformat(row["created_at"]))

    def add_org_membership(self, org_id: str, user_id: str, role: str = "member") -> None:
        self.ensure_organization(org_id)
        self.ensure_user(user_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO org_memberships (org_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (org_id, user_id, role, utc_now().isoformat()),
            )

    def issue_api_key(self, payload: ApiKeyCreate) -> IssuedApiKey:
        self.ensure_user(payload.actor_user_id)
        if payload.actor_org_id:
            self.ensure_organization(payload.actor_org_id)
            self.add_org_membership(payload.actor_org_id, payload.actor_user_id)
        namespace_id = payload.namespace_id or (payload.actor_org_id if payload.namespace_kind == "org" else payload.actor_user_id)
        key_id = self._new_id("key")
        token = f"rrk_{secrets.token_urlsafe(24)}"
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (
                    id, label, token_hash, actor_user_id, actor_org_id, namespace_kind,
                    namespace_id, scopes_json, status, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    payload.label,
                    self._hash_text(token),
                    payload.actor_user_id,
                    payload.actor_org_id,
                    payload.namespace_kind,
                    namespace_id,
                    json.dumps(payload.scopes),
                    "active",
                    created_at.isoformat(),
                    None,
                ),
            )
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return IssuedApiKey(token=token, record=self._api_key_from_row(row))

    def authenticate_api_key(self, token: str) -> AuthContext:
        if not token:
            raise PermissionError("api key required")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE token_hash = ?", (self._hash_text(token),)).fetchone()
        if row is None:
            raise PermissionError("invalid api key")
        if row["status"] != "active":
            raise PermissionError("api key is not active")
        return AuthContext(
            api_key_id=row["id"],
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            scopes=json.loads(row["scopes_json"]),
        )

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER NOT NULL
            );

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
                generated_by_session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL,
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
            """
        )
        conn.execute("DELETE FROM schema_meta")
        conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))

    def _migrate_schema(self, conn: sqlite3.Connection, version: int) -> None:
        if version >= 3:
            conn.execute("DELETE FROM schema_meta")
            conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
            return
        if version < 2:
            self._drop_managed_schema(conn)
            self._create_schema(conn)
            return

        if not self._column_exists(conn, "questions", "parent_question_id"):
            conn.execute("ALTER TABLE questions ADD COLUMN parent_question_id TEXT REFERENCES questions(id) ON DELETE SET NULL")
        if not self._column_exists(conn, "questions", "generated_by_session_id"):
            conn.execute("ALTER TABLE questions ADD COLUMN generated_by_session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL")
        if not self._column_exists(conn, "questions", "generation_reason"):
            conn.execute("ALTER TABLE questions ADD COLUMN generation_reason TEXT")
        if not self._column_exists(conn, "questions", "priority_score"):
            conn.execute("ALTER TABLE questions ADD COLUMN priority_score REAL NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_parent ON questions (parent_question_id, priority_score DESC, created_at DESC)"
        )

        if not self._column_exists(conn, "research_sessions", "ttl_days"):
            conn.execute("ALTER TABLE research_sessions ADD COLUMN ttl_days INTEGER NOT NULL DEFAULT 30")
        if not self._column_exists(conn, "research_sessions", "expires_at"):
            conn.execute("ALTER TABLE research_sessions ADD COLUMN expires_at TEXT")
        if not self._column_exists(conn, "research_sessions", "freshness_state"):
            conn.execute("ALTER TABLE research_sessions ADD COLUMN freshness_state TEXT NOT NULL DEFAULT 'fresh'")
        if not self._column_exists(conn, "research_sessions", "refresh_of_session_id"):
            conn.execute("ALTER TABLE research_sessions ADD COLUMN refresh_of_session_id TEXT REFERENCES research_sessions(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON research_sessions (expires_at, freshness_state)")
        rows = conn.execute("SELECT id, created_at, ttl_days, expires_at FROM research_sessions").fetchall()
        for row in rows:
            ttl_days = row["ttl_days"] or 30
            expires_at = row["expires_at"]
            if expires_at:
                continue
            created_at = datetime.fromisoformat(row["created_at"])
            conn.execute(
                "UPDATE research_sessions SET ttl_days = ?, expires_at = ?, freshness_state = COALESCE(freshness_state, 'fresh') WHERE id = ?",
                ((ttl_days or 30), (created_at + timedelta(days=ttl_days or 30)).isoformat(), row["id"]),
            )

        if not self._column_exists(conn, "reports", "report_kind"):
            conn.execute("ALTER TABLE reports ADD COLUMN report_kind TEXT NOT NULL DEFAULT 'legacy_answer'")
        if not self._column_exists(conn, "reports", "guidance_json"):
            conn.execute("ALTER TABLE reports ADD COLUMN guidance_json TEXT NOT NULL DEFAULT '{}'")
        conn.execute("UPDATE reports SET report_kind = COALESCE(report_kind, 'legacy_answer')")
        conn.execute("UPDATE reports SET guidance_json = COALESCE(guidance_json, '{}')")

        conn.execute("DELETE FROM schema_meta")
        conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))

    def _drop_managed_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in (
            "audit_log",
            "api_keys",
            "org_memberships",
            "organizations",
            "users",
            "report_claims",
            "reports",
            "claim_excerpts",
            "claims",
            "excerpts",
            "sources",
            "research_sessions",
            "questions",
            "topics",
            "report_findings",
            "finding_annotations",
            "findings",
            "annotations",
            "runs",
            "schema_meta",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("PRAGMA foreign_keys = ON")

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _list_questions(
        self,
        *,
        include_private: bool,
        limit: int,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[QuestionRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM questions ORDER BY created_at DESC").fetchall()
        records = [
            self._question_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        ]
        records.sort(key=lambda question: (question.status == "open", question.priority_score, not question.latest_session_is_stale, question.created_at), reverse=True)
        return records[:limit]

    def _list_claims(
        self,
        *,
        include_private: bool,
        limit: int,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[ClaimRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM claims ORDER BY created_at DESC").fetchall()
        records = [
            self._claim_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        ]
        records.sort(key=lambda claim: (not claim.is_stale, claim.created_at), reverse=True)
        return records[:limit]

    def _list_reports(
        self,
        *,
        include_private: bool,
        limit: int,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[ReportRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
        records = [
            self._report_from_row(row)
            for row in rows
            if self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug)
        ]
        records.sort(key=lambda report: (not report.is_stale, report.created_at), reverse=True)
        return records[:limit]

    def _search_questions(
        self,
        normalized: str,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for question in self._list_questions(include_private=include_private, limit=100, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
            haystack = " ".join([question.prompt, question.focus.label or "", *question.focus.parts()]).lower()
            score = self._search_score(
                normalized,
                haystack,
                source_type="question",
                human_reviewed=False,
                created_at=question.created_at,
                provenance_fields=[question.topic_id, question.latest_report_id, question.latest_session_id],
                is_stale=question.latest_session_is_stale,
            )
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="question",
                    id=question.id,
                    title=question.prompt,
                    summary=question.focus.label or question.prompt,
                    subject=question.focus.label or "question",
                    visibility=question.visibility,
                    created_at=question.created_at,
                    score=score,
                    url=f"/questions/{question.id}",
                    human_reviewed=False,
                    freshness_state=question.latest_session_freshness_state,
                    expires_at=question.latest_session_expires_at,
                    is_stale=question.latest_session_is_stale,
                    namespace_kind=question.namespace_kind,
                    namespace_id=question.namespace_id,
                    public_namespace_slug=question.public_namespace_slug,
                    public_index_state=question.public_index_state,
                )
            )
        return hits

    def _search_claims(
        self,
        normalized: str,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for claim in self._list_claims(include_private=include_private, limit=100, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
            haystack = " ".join([claim.title, claim.focal_label, claim.statement]).lower()
            score = self._search_score(
                normalized,
                haystack,
                source_type="claim",
                human_reviewed=claim.human_reviewed,
                created_at=claim.created_at,
                provenance_fields=[claim.session_id, *claim.excerpt_ids],
                is_stale=claim.is_stale,
            )
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="claim",
                    id=claim.id,
                    title=claim.title,
                    summary=claim.statement,
                    subject=claim.focal_label,
                    visibility=claim.visibility,
                    created_at=claim.created_at,
                    score=score,
                    url=f"/claims/{claim.id}",
                    human_reviewed=claim.human_reviewed,
                    freshness_state=claim.freshness_state,
                    expires_at=claim.expires_at,
                    is_stale=claim.is_stale,
                    namespace_kind=claim.namespace_kind,
                    namespace_id=claim.namespace_id,
                    public_namespace_slug=claim.public_namespace_slug,
                    public_index_state=claim.public_index_state,
                )
            )
        return hits

    def _search_reports(
        self,
        normalized: str,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for report in self._list_reports(include_private=include_private, limit=100, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
            haystack = " ".join([report.title, report.focal_label, report.summary_md]).lower()
            score = self._search_score(
                normalized,
                haystack,
                source_type="report",
                human_reviewed=report.human_reviewed,
                created_at=report.created_at,
                provenance_fields=[report.session_id, *report.claim_ids, *report.source_ids],
                is_stale=report.is_stale,
            )
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="report",
                    id=report.id,
                    title=report.title,
                    summary=first_non_heading_line(report.summary_md),
                    subject=report.focal_label,
                    visibility=report.visibility,
                    created_at=report.created_at,
                    score=score,
                    url=f"/reports/{report.id}",
                    human_reviewed=report.human_reviewed,
                    freshness_state=report.freshness_state,
                    expires_at=report.expires_at,
                    is_stale=report.is_stale,
                    namespace_kind=report.namespace_kind,
                    namespace_id=report.namespace_id,
                    public_namespace_slug=report.public_namespace_slug,
                    public_index_state=report.public_index_state,
                )
            )
        return hits

    def _search_sources(
        self,
        normalized: str,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[SearchHit]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            if not self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
                continue
            source = self._source_from_row(row)
            haystack = " ".join([source.title, source.locator, source.source_type, source.snippet or "", source.site_name or "", source.author or ""]).lower()
            score = self._search_score(normalized, haystack, source_type=source.source_type, human_reviewed=False, created_at=source.created_at, provenance_fields=[source.content_sha256, source.snapshot_url])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="source",
                    id=source.id,
                    title=source.title,
                    summary=source.snippet or source.locator,
                    subject=source.source_type,
                    visibility=source.visibility,
                    created_at=source.created_at,
                    score=score,
                    url=f"/sources/{source.id}",
                    namespace_kind=source.namespace_kind,
                    namespace_id=source.namespace_id,
                    public_namespace_slug=source.public_namespace_slug,
                    public_index_state=source.public_index_state,
                )
            )
        return hits

    def _search_excerpts(
        self,
        normalized: str,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> list[SearchHit]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM excerpts ORDER BY created_at DESC").fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            if not self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
                continue
            excerpt = self._excerpt_from_row(row)
            source = self.get_source(excerpt.source_id, include_private=True)
            haystack = " ".join([excerpt.focal_label, excerpt.note, excerpt.quote_text, " ".join(excerpt.tags), source.title]).lower()
            score = self._search_score(
                normalized,
                haystack,
                source_type=source.source_type,
                human_reviewed=excerpt.human_reviewed,
                created_at=excerpt.created_at,
                provenance_fields=[excerpt.source_id, excerpt.session_id, excerpt.model_name],
                is_stale=excerpt.is_stale,
            )
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="excerpt",
                    id=excerpt.id,
                    title=excerpt.focal_label,
                    summary=excerpt.note,
                    subject=excerpt.focal_label,
                    visibility=excerpt.visibility,
                    created_at=excerpt.created_at,
                    score=score,
                    url=f"/excerpts/{excerpt.id}",
                    source_title=source.title,
                    human_reviewed=excerpt.human_reviewed,
                    freshness_state=excerpt.freshness_state,
                    expires_at=excerpt.expires_at,
                    is_stale=excerpt.is_stale,
                    namespace_kind=excerpt.namespace_kind,
                    namespace_id=excerpt.namespace_id,
                    public_namespace_slug=excerpt.public_namespace_slug,
                    public_index_state=excerpt.public_index_state,
                )
            )
        return hits

    def _resolve_source(self, payload: ExcerptCreate, auth: AuthContext | None = None) -> SourceRecord:
        if payload.source_id:
            return self.get_source(payload.source_id, include_private=True)
        assert payload.source is not None
        return self.create_source(payload.source, auth=auth)

    def _fetch_row(self, table: str, record_id: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"{table}:{record_id} not found")
        return row

    def _ensure_visible(
        self,
        row: sqlite3.Row,
        include_private: bool,
        *,
        auth: AuthContext | None = None,
        public_index_only: bool = False,
        namespace_slug: str | None = None,
    ) -> None:
        if not self._can_access_row(row, include_private=include_private, auth=auth, public_index_only=public_index_only, namespace_slug=namespace_slug):
            raise PermissionError("record is private")

    def _can_access_row(
        self,
        row: sqlite3.Row,
        *,
        include_private: bool,
        auth: AuthContext | None,
        public_index_only: bool,
        namespace_slug: str | None,
    ) -> bool:
        visibility = row["visibility"]
        public_namespace_slug = row["public_namespace_slug"]
        public_index_state = row["public_index_state"]

        if visibility == "public":
            if namespace_slug and public_namespace_slug != namespace_slug:
                return False
            if public_index_only and public_index_state != "included":
                return False
            if public_index_state == "suppressed" and not (auth and auth.is_admin):
                return False
            return True

        if not include_private:
            return False
        if auth is None:
            return True
        if auth.is_admin:
            return True
        return auth.has_scope("read_private") and self._row_namespace_matches_auth(row, auth)

    def _set_visibility(
        self,
        conn: sqlite3.Connection,
        kind: str,
        record_id: str,
        visibility: Visibility,
        *,
        include_in_global_index: bool = False,
    ) -> None:
        row = conn.execute(
            f"SELECT namespace_id, public_namespace_slug FROM {self._table_name(kind)} WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"{kind}:{record_id} not found")
        public_namespace_slug = row["public_namespace_slug"] or row["namespace_id"]
        public_index_state = self._public_index_state_for_visibility(visibility, include_in_global_index=include_in_global_index)
        conn.execute(
            f"""
            UPDATE {self._table_name(kind)}
            SET visibility = ?, public_namespace_slug = ?, public_index_state = ?
            WHERE id = ?
            """,
            (visibility, public_namespace_slug, public_index_state, record_id),
        )

    def _cascade_publish(
        self,
        conn: sqlite3.Connection,
        kind: str,
        record_id: str,
        *,
        include_in_global_index: bool = False,
    ) -> None:
        if kind == "source":
            return
        if kind == "excerpt":
            row = conn.execute("SELECT source_id, question_id FROM excerpts WHERE id = ?", (record_id,)).fetchone()
            if row:
                self._set_visibility(conn, "source", row["source_id"], "public", include_in_global_index=include_in_global_index)
                self._set_visibility(conn, "question", row["question_id"], "public", include_in_global_index=include_in_global_index)
            return
        if kind == "claim":
            row = conn.execute("SELECT question_id FROM claims WHERE id = ?", (record_id,)).fetchone()
            if row:
                self._set_visibility(conn, "question", row["question_id"], "public", include_in_global_index=include_in_global_index)
            rows = conn.execute("SELECT excerpt_id FROM claim_excerpts WHERE claim_id = ?", (record_id,)).fetchall()
            for row in rows:
                self._set_visibility(conn, "excerpt", row["excerpt_id"], "public", include_in_global_index=include_in_global_index)
                self._cascade_publish(conn, "excerpt", row["excerpt_id"], include_in_global_index=include_in_global_index)
            return
        if kind == "report":
            row = conn.execute("SELECT question_id FROM reports WHERE id = ?", (record_id,)).fetchone()
            if row:
                self._set_visibility(conn, "question", row["question_id"], "public", include_in_global_index=include_in_global_index)
            rows = conn.execute("SELECT claim_id FROM report_claims WHERE report_id = ?", (record_id,)).fetchall()
            for row in rows:
                self._set_visibility(conn, "claim", row["claim_id"], "public", include_in_global_index=include_in_global_index)
                self._cascade_publish(conn, "claim", row["claim_id"], include_in_global_index=include_in_global_index)

    def _canonical_kind(self, kind: RecordKind | None) -> str | None:
        if kind == "annotation":
            return "excerpt"
        if kind == "finding":
            return "claim"
        return kind

    def _table_name(self, kind: str) -> str:
        return {
            "source": "sources",
            "question": "questions",
            "excerpt": "excerpts",
            "claim": "claims",
            "report": "reports",
        }[kind]

    def _search_score(
        self,
        normalized: str,
        haystack: str,
        *,
        source_type: str | None = None,
        human_reviewed: bool,
        created_at: datetime,
        provenance_fields: list[str | None],
        is_stale: bool = False,
    ) -> float:
        if not normalized:
            lexical = 1.0
        else:
            tokens = [token for token in normalized.split() if token]
            if not tokens:
                lexical = 1.0
            else:
                matched = sum(1 for token in tokens if token in haystack)
                if matched == 0:
                    return 0.0
                lexical = matched * 2.5
                if normalized in haystack:
                    lexical += 2.0
        provenance_score = sum(1 for field in provenance_fields if field) * 0.4
        review_score = 3.0 if human_reviewed else 0.0
        age_days = max((utc_now() - created_at).days, 0)
        recency_score = max(0.0, 3.0 - (age_days / 30.0))
        freshness_penalty = 2.5 if is_stale else 0.0
        return round(lexical + self._source_quality(source_type or "webpage") + provenance_score + review_score + recency_score - freshness_penalty, 3)

    def _source_quality(self, source_type: str) -> int:
        return {
            "paper": 5,
            "official-docs": 5,
            "documentation": 4,
            "dataset": 4,
            "report": 4,
            "test": 4,
            "code": 3,
            "script": 3,
            "article": 3,
            "webpage": 2,
            "question": 2,
            "claim": 2,
            "local_file": 2,
            "note": 1,
        }.get(source_type, 2)

    def _effective_freshness(self, *, expires_at: datetime | None, stored_state: str | None) -> tuple[str | None, datetime | None, bool]:
        if expires_at is not None and expires_at <= utc_now():
            return "needs_refresh", expires_at, True
        if stored_state:
            return stored_state, expires_at, stored_state != "fresh"
        return None, expires_at, False

    def _session_freshness_from_row(self, row: sqlite3.Row | None) -> tuple[str | None, datetime | None, bool]:
        if row is None:
            return None, None, False
        expires_at = self._decode_dt(row["expires_at"])
        return self._effective_freshness(expires_at=expires_at, stored_state=row["freshness_state"])

    def _session_freshness_by_id(self, session_id: str | None) -> tuple[str | None, datetime | None, bool]:
        if not session_id:
            return None, None, False
        with self.connect() as conn:
            row = conn.execute(
                "SELECT expires_at, freshness_state FROM research_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._session_freshness_from_row(row)

    def _topic_from_row(self, row: sqlite3.Row) -> TopicRecord:
        return TopicRecord(
            id=row["id"],
            label=row["label"],
            slug=row["slug"],
            parent_topic_id=row["parent_topic_id"],
            focus=FocusTuple.model_validate_json(row["focus_json"]),
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _question_from_row(self, row: sqlite3.Row) -> QuestionRecord:
        with self.connect() as conn:
            latest_session = conn.execute(
                "SELECT id, expires_at, freshness_state FROM research_sessions WHERE question_id = ? ORDER BY created_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            latest_report = conn.execute(
                "SELECT id FROM reports WHERE question_id = ? ORDER BY created_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
        latest_session_freshness_state, latest_session_expires_at, latest_session_is_stale = self._session_freshness_from_row(latest_session)
        return QuestionRecord(
            id=row["id"],
            topic_id=row["topic_id"],
            prompt=row["prompt"],
            normalized_prompt=row["normalized_prompt"],
            focus=FocusTuple.model_validate_json(row["focus_json"]),
            parent_question_id=row["parent_question_id"],
            generated_by_session_id=row["generated_by_session_id"],
            generation_reason=row["generation_reason"],
            priority_score=row["priority_score"],
            status=row["status"],
            visibility=row["visibility"],
            author_type=row["author_type"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            latest_session_id=latest_session["id"] if latest_session else None,
            latest_report_id=latest_report["id"] if latest_report else None,
            latest_session_freshness_state=latest_session_freshness_state,
            latest_session_expires_at=latest_session_expires_at,
            latest_session_is_stale=latest_session_is_stale,
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _session_from_row(self, row: sqlite3.Row) -> ResearchSessionRecord:
        with self.connect() as conn:
            claim_rows = conn.execute("SELECT id FROM claims WHERE session_id = ? ORDER BY created_at ASC", (row["id"],)).fetchall()
            report_rows = conn.execute("SELECT id FROM reports WHERE session_id = ? ORDER BY created_at ASC", (row["id"],)).fetchall()
            source_rows = conn.execute(
                """
                SELECT DISTINCT e.source_id AS id
                FROM excerpts e
                WHERE e.session_id = ?
                """,
                (row["id"],),
            ).fetchall()
        freshness_state, expires_at, is_stale = self._session_freshness_from_row(row)
        return ResearchSessionRecord(
            id=row["id"],
            question_id=row["question_id"],
            prompt=row["prompt"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            mode=row["mode"],
            ttl_days=row["ttl_days"],
            refresh_of_session_id=row["refresh_of_session_id"],
            status=row["status"],
            source_signals=json.loads(row["source_signals_json"]),
            notes=row["notes"],
            visibility=row["visibility"],
            author_type=row["author_type"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            expires_at=expires_at,
            freshness_state=freshness_state or "fresh",
            is_stale=is_stale,
            claim_ids=[claim_row["id"] for claim_row in claim_rows],
            source_ids=[source_row["id"] for source_row in source_rows],
            report_ids=[report_row["id"] for report_row in report_rows],
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _source_from_row(self, row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            id=row["id"],
            locator=row["locator"],
            title=row["title"],
            source_type=row["source_type"],
            site_name=row["site_name"],
            published_at=self._decode_dt(row["published_at"]),
            accessed_at=self._decode_dt(row["accessed_at"]),
            author=row["author"],
            snippet=row["snippet"],
            content_sha256=row["content_sha256"],
            snapshot_url=row["snapshot_url"],
            snapshot_required=bool(row["snapshot_required"]),
            snapshot_present=bool(row["snapshot_present"]),
            last_verified_at=self._decode_dt(row["last_verified_at"]),
            visibility=row["visibility"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _excerpt_from_row(self, row: sqlite3.Row) -> ExcerptRecord:
        freshness_state, expires_at, is_stale = self._session_freshness_by_id(row["session_id"])
        return ExcerptRecord(
            id=row["id"],
            source_id=row["source_id"],
            question_id=row["question_id"],
            session_id=row["session_id"],
            topic_id=row["topic_id"],
            focal_label=row["focal_label"],
            note=row["note"],
            selector=SourceSelector.model_validate_json(row["selector_json"]),
            quote_text=row["quote_text"],
            confidence=row["confidence"],
            tags=json.loads(row["tags_json"]),
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            freshness_state=freshness_state,
            expires_at=expires_at,
            is_stale=is_stale,
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _claim_from_row(self, row: sqlite3.Row) -> ClaimRecord:
        with self.connect() as conn:
            excerpt_rows = conn.execute(
                "SELECT excerpt_id FROM claim_excerpts WHERE claim_id = ? ORDER BY excerpt_id ASC",
                (row["id"],),
            ).fetchall()
        freshness_state, expires_at, is_stale = self._session_freshness_by_id(row["session_id"])
        return ClaimRecord(
            id=row["id"],
            question_id=row["question_id"],
            session_id=row["session_id"],
            topic_id=row["topic_id"],
            title=row["title"],
            focal_label=row["focal_label"],
            statement=row["statement"],
            excerpt_ids=[excerpt_row["excerpt_id"] for excerpt_row in excerpt_rows],
            status=row["status"],
            confidence=row["confidence"],
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            freshness_state=freshness_state,
            expires_at=expires_at,
            is_stale=is_stale,
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _report_from_row(self, row: sqlite3.Row) -> ReportRecord:
        with self.connect() as conn:
            claim_rows = conn.execute(
                "SELECT claim_id FROM report_claims WHERE report_id = ? ORDER BY claim_id ASC",
                (row["id"],),
            ).fetchall()
        claim_ids = [claim_row["claim_id"] for claim_row in claim_rows]
        source_ids = sorted({excerpt.source_id for claim_id in claim_ids for excerpt in self.list_excerpts_for_claim(claim_id, include_private=True)})
        freshness_state, expires_at, is_stale = self._session_freshness_by_id(row["session_id"])
        return ReportRecord(
            id=row["id"],
            question_id=row["question_id"],
            session_id=row["session_id"],
            title=row["title"],
            focal_label=row["focal_label"],
            summary_md=row["summary_md"],
            report_kind=row["report_kind"],
            guidance=GuidancePayload.model_validate_json(row["guidance_json"]),
            claim_ids=claim_ids,
            source_ids=source_ids,
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            dedupe_key=row["dedupe_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            freshness_state=freshness_state,
            expires_at=expires_at,
            is_stale=is_stale,
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            api_key_id=row["api_key_id"],
            public_namespace_slug=row["public_namespace_slug"],
            public_index_state=row["public_index_state"],
        )

    def _api_key_from_row(self, row: sqlite3.Row) -> ApiKeyRecord:
        return ApiKeyRecord(
            id=row["id"],
            label=row["label"],
            actor_user_id=row["actor_user_id"],
            actor_org_id=row["actor_org_id"],
            namespace_kind=row["namespace_kind"],
            namespace_id=row["namespace_id"],
            scopes=json.loads(row["scopes_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            revoked_at=self._decode_dt(row["revoked_at"]),
        )

    def _public_index_state_for_visibility(self, visibility: str, *, include_in_global_index: bool = False) -> str:
        if visibility == "private":
            return "private"
        return "included" if include_in_global_index else "namespace_only"

    def _write_metadata(self, namespace_kind: str, namespace_id: str, auth: AuthContext | None) -> dict[str, str | None]:
        if auth is None:
            return {
                "namespace_kind": namespace_kind,
                "namespace_id": namespace_id,
                "actor_user_id": None,
                "actor_org_id": None,
                "api_key_id": None,
                "public_namespace_slug": namespace_id,
            }
        if not auth.has_scope("ingest") and not auth.is_admin:
            raise PermissionError("ingest scope required")
        if auth.is_admin:
            resolved_namespace_kind = namespace_kind
            resolved_namespace_id = namespace_id
        else:
            resolved_namespace_kind = auth.namespace_kind
            resolved_namespace_id = auth.namespace_id
        return {
            "namespace_kind": resolved_namespace_kind,
            "namespace_id": resolved_namespace_id,
            "actor_user_id": auth.actor_user_id,
            "actor_org_id": auth.actor_org_id,
            "api_key_id": auth.api_key_id,
            "public_namespace_slug": resolved_namespace_id,
        }

    def _row_namespace_matches_auth(self, row: sqlite3.Row, auth: AuthContext) -> bool:
        return row["namespace_kind"] == auth.namespace_kind and row["namespace_id"] == auth.namespace_id

    def _fetch_existing_by_dedupe_key(self, conn: sqlite3.Connection, table: str, dedupe_key: str | None) -> sqlite3.Row | None:
        if not dedupe_key:
            return None
        return conn.execute(f"SELECT * FROM {table} WHERE dedupe_key = ? LIMIT 1", (dedupe_key,)).fetchone()

    def _record_audit(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        kind: str | None,
        record_id: str | None,
        auth: AuthContext | None,
        details: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (
                id, action, kind, record_id, api_key_id, actor_user_id,
                actor_org_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._new_id("audit"),
                action,
                kind,
                record_id,
                auth.api_key_id if auth else None,
                auth.actor_user_id if auth else None,
                auth.actor_org_id if auth else None,
                json.dumps(details, sort_keys=True),
                utc_now().isoformat(),
            ),
        )

    def _encode_dt(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    def _decode_dt(self, value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    def _hash_text(self, value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"
