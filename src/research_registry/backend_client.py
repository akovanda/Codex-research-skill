from __future__ import annotations

from typing import Protocol

import httpx

from .backend_selection import load_backend_profiles, resolve_backend
from .config import Settings
from .models import (
    BackendStatus,
    ClaimCreate,
    ClaimRecord,
    ExcerptCreate,
    ExcerptRecord,
    PublishRequest,
    QuestionCreate,
    QuestionRecord,
    ReportCreate,
    ReportRecord,
    ResearchSessionCreate,
    ResearchSessionRecord,
    SearchResponse,
    SourceCreate,
    SourceRecord,
)
from .service import RegistryService


class RegistryBackend(Protocol):
    def search(self, query: str, *, kind: str | None = None, include_private: bool = False, limit: int = 20) -> SearchResponse: ...
    def get_question(self, question_id: str, include_private: bool = False) -> QuestionRecord: ...
    def get_source(self, source_id: str, include_private: bool = False) -> SourceRecord: ...
    def get_excerpt(self, excerpt_id: str, include_private: bool = False) -> ExcerptRecord: ...
    def get_claim(self, claim_id: str, include_private: bool = False) -> ClaimRecord: ...
    def get_report(self, report_id: str, include_private: bool = False) -> ReportRecord: ...
    def create_question(self, payload: QuestionCreate) -> QuestionRecord: ...
    def create_session(self, payload: ResearchSessionCreate) -> ResearchSessionRecord: ...
    def create_source(self, payload: SourceCreate) -> SourceRecord: ...
    def create_excerpt(self, payload: ExcerptCreate) -> ExcerptRecord: ...
    def create_claim(self, payload: ClaimCreate) -> ClaimRecord: ...
    def create_report(self, payload: ReportCreate) -> ReportRecord: ...
    def set_question_status(self, question_id: str, status: str) -> None: ...
    def publish(self, payload: PublishRequest) -> None: ...
    def backend_status(self) -> BackendStatus: ...


class RegistryApiClient:
    def __init__(self, base_url: str, api_key: str | None, status: BackendStatus):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._status = status

    def search(self, query: str, *, kind: str | None = None, include_private: bool = False, limit: int = 20) -> SearchResponse:
        payload = self._request(
            "GET",
            "/api/search",
            params={"q": query, "kind": kind, "include_private": str(include_private).lower(), "limit": str(limit)},
        )
        return SearchResponse.model_validate(payload)

    def get_question(self, question_id: str, include_private: bool = False) -> QuestionRecord:
        payload = self._request("GET", f"/api/questions/{question_id}", params={"include_private": str(include_private).lower()})
        return QuestionRecord.model_validate(payload)

    def get_source(self, source_id: str, include_private: bool = False) -> SourceRecord:
        payload = self._request("GET", f"/api/sources/{source_id}", params={"include_private": str(include_private).lower()})
        return SourceRecord.model_validate(payload)

    def get_excerpt(self, excerpt_id: str, include_private: bool = False) -> ExcerptRecord:
        payload = self._request("GET", f"/api/excerpts/{excerpt_id}", params={"include_private": str(include_private).lower()})
        return ExcerptRecord.model_validate(payload)

    def get_claim(self, claim_id: str, include_private: bool = False) -> ClaimRecord:
        payload = self._request("GET", f"/api/claims/{claim_id}", params={"include_private": str(include_private).lower()})
        return ClaimRecord.model_validate(payload)

    def get_report(self, report_id: str, include_private: bool = False) -> ReportRecord:
        payload = self._request("GET", f"/api/reports/{report_id}", params={"include_private": str(include_private).lower()})
        return ReportRecord.model_validate(payload)

    def create_question(self, payload: QuestionCreate) -> QuestionRecord:
        return QuestionRecord.model_validate(self._request("POST", "/api/questions", json=payload.model_dump(mode="json")))

    def create_session(self, payload: ResearchSessionCreate) -> ResearchSessionRecord:
        return ResearchSessionRecord.model_validate(self._request("POST", "/api/sessions", json=payload.model_dump(mode="json")))

    def create_source(self, payload: SourceCreate) -> SourceRecord:
        return SourceRecord.model_validate(self._request("POST", "/api/sources", json=payload.model_dump(mode="json")))

    def create_excerpt(self, payload: ExcerptCreate) -> ExcerptRecord:
        return ExcerptRecord.model_validate(self._request("POST", "/api/excerpts", json=payload.model_dump(mode="json")))

    def create_claim(self, payload: ClaimCreate) -> ClaimRecord:
        return ClaimRecord.model_validate(self._request("POST", "/api/claims", json=payload.model_dump(mode="json")))

    def create_report(self, payload: ReportCreate) -> ReportRecord:
        return ReportRecord.model_validate(self._request("POST", "/api/reports", json=payload.model_dump(mode="json")))

    def set_question_status(self, question_id: str, status: str) -> None:
        self._request("POST", f"/api/questions/{question_id}/status", json={"status": status})

    def publish(self, payload: PublishRequest) -> None:
        self._request("POST", "/api/publish", json=payload.model_dump(mode="json"))

    def backend_status(self) -> BackendStatus:
        payload = self._request("GET", "/api/backend/status")
        return BackendStatus.model_validate(payload)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        if self.api_key:
            headers["x-api-key"] = self.api_key
        with httpx.Client(base_url=self.base_url, timeout=20.0) as client:
            response = client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()


def create_backend(settings: Settings) -> RegistryBackend:
    status = resolve_backend(settings)
    if status.kind == "local" or status.url is None:
        service = RegistryService(settings.database_url)
        service.initialize()
        service.set_backend_status(status)
        return service

    profiles = load_backend_profiles(settings.backend_profile_path)
    profile_key = None
    if settings.backend_profile and settings.backend_profile in profiles.profiles:
        profile_key = profiles.profiles[settings.backend_profile].api_key
    elif settings.backend_org and settings.backend_org in profiles.organizations:
        profile_key = profiles.organizations[settings.backend_org].api_key

    api_key = settings.backend_api_key or profile_key
    return RegistryApiClient(status.url, api_key, status)
