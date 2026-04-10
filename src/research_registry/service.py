from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .models import (
    AnnotationCreate,
    AnnotationRecord,
    DashboardData,
    FindingCreate,
    FindingRecord,
    PublishRequest,
    RecordKind,
    ReportCompileCreate,
    ReportRecord,
    ReviewRequest,
    RunCreate,
    RunRecord,
    SearchHit,
    SearchResponse,
    SourceCreate,
    SourceRecord,
    SourceSelector,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class RegistryService:
    def __init__(self, db_path: Path):
        self.db_path = db_path

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

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    notes TEXT,
                    visibility TEXT NOT NULL,
                    author_type TEXT NOT NULL,
                    freshness_ttl_days INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
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
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sources_url_hash
                    ON sources (canonical_url, content_sha256);

                CREATE TABLE IF NOT EXISTS annotations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    subject TEXT NOT NULL,
                    note TEXT NOT NULL,
                    selector_json TEXT NOT NULL,
                    quote_text TEXT,
                    quote_hash TEXT,
                    anchor_fingerprint TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    freshness_ttl_days INTEGER NOT NULL,
                    visibility TEXT NOT NULL,
                    author_type TEXT NOT NULL,
                    model_name TEXT,
                    model_version TEXT,
                    parent_annotation_id TEXT REFERENCES annotations(id) ON DELETE SET NULL,
                    tags_json TEXT NOT NULL,
                    source_content_sha256 TEXT,
                    human_reviewed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_annotations_source ON annotations (source_id);
                CREATE INDEX IF NOT EXISTS idx_annotations_anchor ON annotations (anchor_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_annotations_visibility_created
                    ON annotations (visibility, created_at DESC);

                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    author_type TEXT NOT NULL,
                    model_name TEXT,
                    model_version TEXT,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    confidence REAL NOT NULL,
                    human_reviewed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finding_annotations (
                    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
                    annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE RESTRICT,
                    PRIMARY KEY (finding_id, annotation_id)
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    summary_md TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    author_type TEXT NOT NULL,
                    model_name TEXT,
                    model_version TEXT,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    human_reviewed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_findings (
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE RESTRICT,
                    PRIMARY KEY (report_id, finding_id)
                );
                """
            )

    def create_run(self, payload: RunCreate) -> RunRecord:
        run_id = self._new_id("run")
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, question, model_name, model_version, notes, visibility,
                    author_type, freshness_ttl_days, started_at, finished_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload.question,
                    payload.model_name,
                    payload.model_version,
                    payload.notes,
                    payload.visibility,
                    payload.author_type,
                    payload.freshness_ttl_days,
                    created_at.isoformat(),
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row)

    def create_source(self, payload: SourceCreate) -> SourceRecord:
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM sources
                WHERE canonical_url = ? AND COALESCE(content_sha256, '') = COALESCE(?, '')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (payload.canonical_url, payload.content_sha256),
            ).fetchone()
            if existing:
                return self._source_from_row(existing)

            source_id = self._new_id("src")
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO sources (
                    id, canonical_url, title, source_type, site_name, published_at, accessed_at,
                    author, snippet, content_sha256, snapshot_url, snapshot_required,
                    snapshot_present, last_verified_at, visibility, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    payload.canonical_url,
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
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_from_row(row)

    def get_source(self, source_id: str, include_private: bool = False) -> SourceRecord:
        row = self._fetch_row("sources", source_id)
        self._ensure_visible(row, include_private)
        return self._source_from_row(row)

    def create_annotation(self, payload: AnnotationCreate) -> AnnotationRecord:
        source = self._resolve_source(payload)
        created_at = utc_now()
        quote_text = payload.quote_text or payload.selector.exact
        quote_hash = self._hash_text(quote_text) if quote_text else None
        anchor_fingerprint = self._anchor_fingerprint(source.id, payload.selector, quote_hash)
        annotation_id = self._new_id("ann")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotations (
                    id, source_id, run_id, subject, note, selector_json, quote_text, quote_hash,
                    anchor_fingerprint, confidence, freshness_ttl_days, visibility, author_type,
                    model_name, model_version, parent_annotation_id, tags_json,
                    source_content_sha256, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_id,
                    source.id,
                    payload.run_id,
                    payload.subject,
                    payload.note,
                    payload.selector.model_dump_json(),
                    quote_text,
                    quote_hash,
                    anchor_fingerprint,
                    payload.confidence,
                    payload.freshness_ttl_days,
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    payload.parent_annotation_id,
                    json.dumps(payload.tags),
                    source.content_sha256,
                    0,
                    created_at.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
        return self._annotation_from_row(row, source)

    def get_annotation(self, annotation_id: str, include_private: bool = False) -> AnnotationRecord:
        row = self._fetch_row("annotations", annotation_id)
        self._ensure_visible(row, include_private)
        source = self._fetch_row("sources", row["source_id"])
        return self._annotation_from_row(row, source)

    def create_finding(self, payload: FindingCreate) -> FindingRecord:
        annotations = [self.get_annotation(annotation_id, include_private=True) for annotation_id in payload.annotation_ids]
        confidence = payload.confidence
        if confidence is None:
            confidence = round(sum(annotation.confidence for annotation in annotations) / len(annotations), 3)
        finding_id = self._new_id("fdg")
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO findings (
                    id, title, subject, claim, visibility, author_type,
                    model_name, model_version, run_id, confidence, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    payload.title,
                    payload.subject,
                    payload.claim,
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    payload.run_id,
                    confidence,
                    0,
                    created_at.isoformat(),
                ),
            )
            conn.executemany(
                "INSERT INTO finding_annotations (finding_id, annotation_id) VALUES (?, ?)",
                [(finding_id, annotation_id) for annotation_id in payload.annotation_ids],
            )
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        return self._finding_from_row(row, annotations=annotations)

    def get_finding(self, finding_id: str, include_private: bool = False) -> FindingRecord:
        row = self._fetch_row("findings", finding_id)
        self._ensure_visible(row, include_private)
        annotations = self._annotations_for_finding(finding_id, include_private=include_private)
        return self._finding_from_row(row, annotations=annotations)

    def compile_report(self, payload: ReportCompileCreate) -> ReportRecord:
        findings = [self.get_finding(finding_id, include_private=True) for finding_id in payload.finding_ids]
        annotation_ids = sorted({annotation_id for finding in findings for annotation_id in finding.annotation_ids})
        annotations = [self.get_annotation(annotation_id, include_private=True) for annotation_id in annotation_ids]
        sources = [self.get_source(source_id, include_private=True) for source_id in sorted({annotation.source_id for annotation in annotations})]
        report_id = self._new_id("rpt")
        created_at = utc_now()
        summary_md = self._compile_summary(payload.question, findings, annotations, sources)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    id, question, subject, summary_md, visibility, author_type,
                    model_name, model_version, run_id, human_reviewed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    payload.question,
                    payload.subject,
                    summary_md,
                    payload.visibility,
                    payload.author_type,
                    payload.model_name,
                    payload.model_version,
                    payload.run_id,
                    0,
                    created_at.isoformat(),
                ),
            )
            conn.executemany(
                "INSERT INTO report_findings (report_id, finding_id) VALUES (?, ?)",
                [(report_id, finding_id) for finding_id in payload.finding_ids],
            )
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return self._report_from_row(row, findings=findings)

    def get_report(self, report_id: str, include_private: bool = False) -> ReportRecord:
        row = self._fetch_row("reports", report_id)
        self._ensure_visible(row, include_private)
        findings = self._findings_for_report(report_id, include_private=include_private)
        return self._report_from_row(row, findings=findings)

    def search(
        self,
        query: str,
        *,
        kind: RecordKind | None = None,
        include_private: bool = False,
        limit: int = 20,
    ) -> SearchResponse:
        normalized = query.strip().lower()
        hits: list[SearchHit] = []
        if kind in (None, "annotation"):
            hits.extend(self._search_annotations(normalized, include_private))
        if kind in (None, "finding"):
            hits.extend(self._search_findings(normalized, include_private))
        if kind in (None, "report"):
            hits.extend(self._search_reports(normalized, include_private))
        if kind in (None, "source"):
            hits.extend(self._search_sources(normalized, include_private))
        hits.sort(key=lambda hit: (hit.score, hit.created_at), reverse=True)
        return SearchResponse(query=query, hits=hits[:limit])

    def dashboard(self, *, include_private: bool, limit: int = 8) -> DashboardData:
        reports = self._list_reports(include_private=include_private, limit=limit)
        findings = self._list_findings(include_private=include_private, limit=limit)
        annotations = self._list_annotations(include_private=include_private, limit=limit)
        return DashboardData(reports=reports, findings=findings, annotations=annotations)

    def publish(self, payload: PublishRequest) -> None:
        with self.connect() as conn:
            self._set_visibility(conn, payload.kind, payload.record_id, "public")
            if payload.cascade_linked_sources:
                self._cascade_publish(conn, payload.kind, payload.record_id)

    def review(self, payload: ReviewRequest) -> None:
        with self.connect() as conn:
            conn.execute(
                f"UPDATE {self._table_name(payload.kind)} SET human_reviewed = ? WHERE id = ?",
                (int(payload.reviewed), payload.record_id),
            )

    def seed_demo(self) -> dict[str, str]:
        if self.search("zebra", include_private=True).hits:
            return {}
        run = self.create_run(
            RunCreate(
                question="What do zebra coat patterns do in the wild?",
                model_name="gpt-5.4",
                model_version="2026-04-10",
            )
        )
        source_one = self.create_source(
            SourceCreate(
                canonical_url="https://example.org/zebra-striped-passage",
                title="Field observations on zebra striping",
                source_type="paper",
                site_name="Example Ecology",
                author="A. Researcher",
                snippet="Zebra stripes may reduce biting fly landings and support social recognition.",
                content_sha256=self._hash_text("field observations zebra stripes"),
                snapshot_url="https://archive.example.org/zebra-striped-passage",
                snapshot_required=True,
                snapshot_present=True,
                visibility="private",
            )
        )
        source_two = self.create_source(
            SourceCreate(
                canonical_url="https://example.org/zebra-fly-study",
                title="Why zebras have stripes",
                source_type="article",
                site_name="Wildlife Notes",
                snippet="Controlled experiments suggest stripes interfere with horsefly approach and landing.",
                snapshot_required=True,
                snapshot_present=False,
                visibility="private",
            )
        )
        annotation_one = self.create_annotation(
            AnnotationCreate(
                source_id=source_one.id,
                run_id=run.id,
                subject="zebra stripes",
                note="This passage supports the fly-avoidance hypothesis and links the claim to observed landing behavior.",
                selector=SourceSelector(
                    exact="Zebra stripes may reduce biting fly landings and support social recognition.",
                    deep_link="https://example.org/zebra-striped-passage#p2",
                    start=128,
                    end=212,
                ),
                confidence=0.88,
                model_name="gpt-5.4",
                model_version="2026-04-10",
                tags=["zebra", "flies", "behavior"],
            )
        )
        annotation_two = self.create_annotation(
            AnnotationCreate(
                source_id=source_two.id,
                run_id=run.id,
                subject="zebra stripes",
                note="This source reinforces the same claim but is already stale because the snapshot is missing.",
                selector=SourceSelector(
                    exact="Controlled experiments suggest stripes interfere with horsefly approach and landing.",
                    deep_link="https://example.org/zebra-fly-study#results",
                ),
                confidence=0.72,
                model_name="gpt-5.4",
                model_version="2026-04-10",
                tags=["zebra", "horsefly"],
            )
        )
        finding = self.create_finding(
            FindingCreate(
                title="Stripes likely reduce fly landings",
                subject="zebra stripes",
                claim="The strongest supported explanation in this mini corpus is that zebra stripes reduce fly landings, while social-signaling remains plausible but less directly evidenced here.",
                annotation_ids=[annotation_one.id, annotation_two.id],
                model_name="gpt-5.4",
                model_version="2026-04-10",
                run_id=run.id,
            )
        )
        report = self.compile_report(
            ReportCompileCreate(
                question="What does this small source set say about why zebras have stripes?",
                subject="zebra stripes",
                finding_ids=[finding.id],
                model_name="gpt-5.4",
                model_version="2026-04-10",
                run_id=run.id,
            )
        )
        self.review(ReviewRequest(kind="annotation", record_id=annotation_one.id))
        self.review(ReviewRequest(kind="finding", record_id=finding.id))
        self.publish(PublishRequest(kind="report", record_id=report.id))
        return {"run_id": run.id, "report_id": report.id}

    def _resolve_source(self, payload: AnnotationCreate) -> SourceRecord:
        if payload.source_id:
            return self.get_source(payload.source_id, include_private=True)
        assert payload.source is not None
        return self.create_source(payload.source)

    def _list_annotations(self, *, include_private: bool, limit: int) -> list[AnnotationRecord]:
        with self.connect() as conn:
            query = "SELECT * FROM annotations"
            params: list[object] = []
            if not include_private:
                query += " WHERE visibility = 'public'"
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            sources = self._fetch_sources_map(conn, [row["source_id"] for row in rows])
        return [self._annotation_from_row(row, sources[row["source_id"]]) for row in rows]

    def _list_findings(self, *, include_private: bool, limit: int) -> list[FindingRecord]:
        with self.connect() as conn:
            query = "SELECT * FROM findings"
            params: list[object] = []
            if not include_private:
                query += " WHERE visibility = 'public'"
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [self.get_finding(row["id"], include_private=include_private) for row in rows]

    def _list_reports(self, *, include_private: bool, limit: int) -> list[ReportRecord]:
        with self.connect() as conn:
            query = "SELECT * FROM reports"
            params: list[object] = []
            if not include_private:
                query += " WHERE visibility = 'public'"
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [self.get_report(row["id"], include_private=include_private) for row in rows]

    def _search_annotations(self, normalized: str, include_private: bool) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for annotation in self._list_annotations(include_private=include_private, limit=100):
            source = self.get_source(annotation.source_id, include_private=True)
            haystack = " ".join(
                [
                    annotation.subject,
                    annotation.note,
                    annotation.quote_text or "",
                    " ".join(annotation.tags),
                    source.title,
                    source.site_name or "",
                ]
            ).lower()
            score = self._search_score(normalized, haystack, source_type=source.source_type, human_reviewed=annotation.human_reviewed, created_at=annotation.created_at, provenance_fields=[annotation.source_id, annotation.quote_hash, annotation.model_name, annotation.run_id])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="annotation",
                    id=annotation.id,
                    title=f"{annotation.subject}: anchored note",
                    summary=annotation.note,
                    subject=annotation.subject,
                    visibility=annotation.visibility,
                    created_at=annotation.created_at,
                    score=score,
                    url=f"/annotations/{annotation.id}",
                    source_title=source.title,
                    human_reviewed=annotation.human_reviewed,
                )
            )
        return hits

    def _search_findings(self, normalized: str, include_private: bool) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for finding in self._list_findings(include_private=include_private, limit=100):
            annotations = [self.get_annotation(annotation_id, include_private=True) for annotation_id in finding.annotation_ids]
            source_quality = max((self._source_quality(self.get_source(annotation.source_id, include_private=True).source_type) for annotation in annotations), default=0)
            haystack = " ".join([finding.title, finding.subject, finding.claim]).lower()
            score = self._search_score(normalized, haystack, source_quality=source_quality, human_reviewed=finding.human_reviewed, created_at=finding.created_at, provenance_fields=[finding.run_id, finding.model_name, *finding.annotation_ids])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="finding",
                    id=finding.id,
                    title=finding.title,
                    summary=finding.claim,
                    subject=finding.subject,
                    visibility=finding.visibility,
                    created_at=finding.created_at,
                    score=score,
                    url=f"/findings/{finding.id}",
                    human_reviewed=finding.human_reviewed,
                )
            )
        return hits

    def _search_reports(self, normalized: str, include_private: bool) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for report in self._list_reports(include_private=include_private, limit=100):
            source_quality = max((self._source_quality(self.get_source(source_id, include_private=True).source_type) for source_id in report.source_ids), default=0)
            haystack = " ".join([report.question, report.subject, report.summary_md]).lower()
            score = self._search_score(normalized, haystack, source_quality=source_quality, human_reviewed=report.human_reviewed, created_at=report.created_at, provenance_fields=[report.run_id, report.model_name, *report.finding_ids, *report.annotation_ids])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="report",
                    id=report.id,
                    title=report.question,
                    summary=report.summary_md.splitlines()[0].replace("#", "").strip(),
                    subject=report.subject,
                    visibility=report.visibility,
                    created_at=report.created_at,
                    score=score,
                    url=f"/reports/{report.id}",
                    human_reviewed=report.human_reviewed,
                )
            )
        return hits

    def _search_sources(self, normalized: str, include_private: bool) -> list[SearchHit]:
        with self.connect() as conn:
            query = "SELECT * FROM sources"
            if not include_private:
                query += " WHERE visibility = 'public'"
            rows = conn.execute(query).fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            source = self._source_from_row(row)
            haystack = " ".join([source.title, source.canonical_url, source.source_type, source.snippet or "", source.site_name or "", source.author or ""]).lower()
            score = self._search_score(normalized, haystack, source_type=source.source_type, human_reviewed=False, created_at=source.created_at, provenance_fields=[source.content_sha256, source.snapshot_url])
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    kind="source",
                    id=source.id,
                    title=source.title,
                    summary=source.snippet or source.canonical_url,
                    subject=source.source_type,
                    visibility=source.visibility,
                    created_at=source.created_at,
                    score=score,
                    url=f"/sources/{source.id}",
                )
            )
        return hits

    def _fetch_row(self, table: str, record_id: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"{table}:{record_id} not found")
        return row

    def _ensure_visible(self, row: sqlite3.Row, include_private: bool) -> None:
        if row["visibility"] == "private" and not include_private:
            raise PermissionError("record is private")

    def _fetch_sources_map(self, conn: sqlite3.Connection, source_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not source_ids:
            return {}
        placeholders = ", ".join("?" for _ in source_ids)
        rows = conn.execute(f"SELECT * FROM sources WHERE id IN ({placeholders})", source_ids).fetchall()
        return {row["id"]: row for row in rows}

    def _annotations_for_finding(self, finding_id: str, *, include_private: bool) -> list[AnnotationRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*
                FROM annotations a
                JOIN finding_annotations fa ON fa.annotation_id = a.id
                WHERE fa.finding_id = ?
                ORDER BY a.created_at ASC
                """,
                (finding_id,),
            ).fetchall()
            sources = self._fetch_sources_map(conn, [row["source_id"] for row in rows])
        annotations = [self._annotation_from_row(row, sources[row["source_id"]]) for row in rows]
        if include_private:
            return annotations
        return [annotation for annotation in annotations if annotation.visibility == "public"]

    def _findings_for_report(self, report_id: str, *, include_private: bool) -> list[FindingRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.*
                FROM findings f
                JOIN report_findings rf ON rf.finding_id = f.id
                WHERE rf.report_id = ?
                ORDER BY f.created_at ASC
                """,
                (report_id,),
            ).fetchall()
        findings = [self.get_finding(row["id"], include_private=True) for row in rows]
        if include_private:
            return findings
        return [finding for finding in findings if finding.visibility == "public"]

    def _compile_summary(
        self,
        question: str,
        findings: list[FindingRecord],
        annotations: list[AnnotationRecord],
        sources: list[SourceRecord],
    ) -> str:
        lines = [
            f"# {question}",
            "",
            "## Snapshot",
            f"This report was compiled from {len(findings)} finding(s), {len(annotations)} annotation(s), and {len(sources)} source(s).",
            "",
            "## Findings",
        ]
        for index, finding in enumerate(findings, start=1):
            lines.append(f"{index}. {finding.claim}")
        lines.extend(["", "## Provenance"])
        for source in sources:
            lines.append(f"- {source.title}: {source.canonical_url}")
        return "\n".join(lines)

    def _set_visibility(self, conn: sqlite3.Connection, kind: RecordKind, record_id: str, visibility: str) -> None:
        conn.execute(f"UPDATE {self._table_name(kind)} SET visibility = ? WHERE id = ?", (visibility, record_id))

    def _cascade_publish(self, conn: sqlite3.Connection, kind: RecordKind, record_id: str) -> None:
        if kind == "source":
            return
        if kind == "annotation":
            row = conn.execute("SELECT source_id FROM annotations WHERE id = ?", (record_id,)).fetchone()
            if row:
                self._set_visibility(conn, "source", row["source_id"], "public")
            return
        if kind == "finding":
            rows = conn.execute(
                "SELECT annotation_id FROM finding_annotations WHERE finding_id = ?",
                (record_id,),
            ).fetchall()
            for row in rows:
                self._set_visibility(conn, "annotation", row["annotation_id"], "public")
                self._cascade_publish(conn, "annotation", row["annotation_id"])
            return
        if kind == "report":
            rows = conn.execute(
                "SELECT finding_id FROM report_findings WHERE report_id = ?",
                (record_id,),
            ).fetchall()
            for row in rows:
                self._set_visibility(conn, "finding", row["finding_id"], "public")
                self._cascade_publish(conn, "finding", row["finding_id"])

    def _table_name(self, kind: RecordKind) -> str:
        return {"source": "sources", "annotation": "annotations", "finding": "findings", "report": "reports"}[kind]

    def _search_score(
        self,
        normalized: str,
        haystack: str,
        *,
        source_type: str | None = None,
        source_quality: int | None = None,
        human_reviewed: bool,
        created_at: datetime,
        provenance_fields: list[str | None],
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
        source_quality_score = source_quality if source_quality is not None else self._source_quality(source_type or "webpage")
        provenance_score = sum(1 for field in provenance_fields if field) * 0.4
        review_score = 3.0 if human_reviewed else 0.0
        age_days = max((utc_now() - created_at).days, 0)
        recency_score = max(0.0, 3.0 - (age_days / 30.0))
        return round(lexical + source_quality_score + provenance_score + review_score + recency_score, 3)

    def _source_quality(self, source_type: str) -> int:
        return {
            "paper": 5,
            "official-docs": 5,
            "documentation": 4,
            "dataset": 4,
            "article": 3,
            "webpage": 2,
            "note": 1,
        }.get(source_type, 2)

    def _annotation_from_row(self, row: sqlite3.Row, source_row: sqlite3.Row | SourceRecord) -> AnnotationRecord:
        source = source_row if isinstance(source_row, SourceRecord) else self._source_from_row(source_row)
        reason = self._annotation_staleness_reason(row, source)
        return AnnotationRecord(
            id=row["id"],
            source_id=row["source_id"],
            run_id=row["run_id"],
            subject=row["subject"],
            note=row["note"],
            selector=SourceSelector.model_validate_json(row["selector_json"]),
            quote_text=row["quote_text"],
            quote_hash=row["quote_hash"],
            anchor_fingerprint=row["anchor_fingerprint"],
            confidence=row["confidence"],
            freshness_ttl_days=row["freshness_ttl_days"],
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            parent_annotation_id=row["parent_annotation_id"],
            tags=json.loads(row["tags_json"]),
            source_content_sha256=row["source_content_sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            is_stale=reason is not None,
            staleness_reason=reason,
        )

    def _finding_from_row(
        self,
        row: sqlite3.Row,
        *,
        annotations: list[AnnotationRecord] | None = None,
    ) -> FindingRecord:
        if annotations is None:
            annotations = self._annotations_for_finding(row["id"], include_private=True)
        reasons = [annotation.staleness_reason for annotation in annotations if annotation.staleness_reason]
        return FindingRecord(
            id=row["id"],
            title=row["title"],
            subject=row["subject"],
            claim=row["claim"],
            annotation_ids=[annotation.id for annotation in annotations],
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            run_id=row["run_id"],
            confidence=row["confidence"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            is_stale=bool(reasons),
            staleness_reason=reasons[0] if reasons else None,
        )

    def _report_from_row(
        self,
        row: sqlite3.Row,
        *,
        findings: list[FindingRecord] | None = None,
    ) -> ReportRecord:
        if findings is None:
            findings = self._findings_for_report(row["id"], include_private=True)
        annotations = [self.get_annotation(annotation_id, include_private=True) for finding in findings for annotation_id in finding.annotation_ids]
        dedup_annotations = {annotation.id: annotation for annotation in annotations}
        source_ids = sorted({annotation.source_id for annotation in dedup_annotations.values()})
        reasons = [reason for finding in findings if finding.staleness_reason for reason in [finding.staleness_reason]]
        return ReportRecord(
            id=row["id"],
            question=row["question"],
            subject=row["subject"],
            summary_md=row["summary_md"],
            finding_ids=[finding.id for finding in findings],
            annotation_ids=sorted(dedup_annotations),
            source_ids=source_ids,
            visibility=row["visibility"],
            author_type=row["author_type"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            run_id=row["run_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            human_reviewed=bool(row["human_reviewed"]),
            is_stale=bool(reasons),
            staleness_reason=reasons[0] if reasons else None,
        )

    def _source_from_row(self, row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            id=row["id"],
            canonical_url=row["canonical_url"],
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
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _run_from_row(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            question=row["question"],
            model_name=row["model_name"],
            model_version=row["model_version"],
            notes=row["notes"],
            visibility=row["visibility"],
            author_type=row["author_type"],
            freshness_ttl_days=row["freshness_ttl_days"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _annotation_staleness_reason(self, row: sqlite3.Row, source: SourceRecord) -> str | None:
        if source.snapshot_required and not source.snapshot_present:
            return "snapshot_missing"
        if source.content_sha256 and row["source_content_sha256"] and source.content_sha256 != row["source_content_sha256"]:
            return "source_changed"
        created_at = datetime.fromisoformat(row["created_at"])
        fresh_until = created_at + timedelta(days=row["freshness_ttl_days"])
        if utc_now() > fresh_until:
            return "freshness_expired"
        return None

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"

    def _hash_text(self, value: str) -> str:
        return sha256(value.strip().encode("utf-8")).hexdigest()

    def _anchor_fingerprint(self, source_id: str, selector: SourceSelector, quote_hash: str | None) -> str:
        payload = {"source_id": source_id, "selector": selector.model_dump(mode="json"), "quote_hash": quote_hash}
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _decode_dt(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)

    def _encode_dt(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()
