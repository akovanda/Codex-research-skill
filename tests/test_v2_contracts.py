from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from research_registry import models as v1_models
from research_registry.contracts import v1
from research_registry.contracts.v2 import (
    DepositEvidence,
    DepositSource,
    ResearchDepositRequest,
    ResearchDepositResult,
    ResearchErrorResponse,
    ResearchGetRequest,
    ResearchRefreshRequest,
    ResearchReviewRequest,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchStatusResponse,
    SourceSelectorV2,
)


CONTRACTS = Path(__file__).parent / "contracts"
V2_CONTRACTS = CONTRACTS / "v2"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _v1_characterization() -> dict[str, Any]:
    return {
        "question_aliases": v1_models.QuestionCreate.model_validate(
            {
                "text": "Research alias compatibility.",
                "focus_label": "Alias behavior",
            }
        ).model_dump(mode="json"),
        "session_aliases": v1_models.ResearchSessionCreate.model_validate(
            {
                "question_id": "q_123",
                "title": "Alias session",
                "mode": "implicit",
            }
        ).model_dump(mode="json"),
        "source_aliases": v1_models.SourceCreate.model_validate(
            {
                "url": "https://example.test/source",
                "label": "Alias source",
            }
        ).model_dump(mode="json"),
        "excerpt_aliases": v1_models.ExcerptCreate.model_validate(
            {
                "question_id": "q_123",
                "locator": "https://example.test/source#L1",
                "title": "Alias excerpt",
                "summary": "Legacy note",
                "selector": "https://example.test/source#L1",
                "text": "legacy quote",
            }
        ).model_dump(mode="json"),
        "claim_aliases": v1_models.ClaimCreate.model_validate(
            {
                "question_id": "q_123",
                "summary": "Legacy claim text",
                "subject": "Alias behavior",
                "evidence_excerpt_ids": "ex_123",
            }
        ).model_dump(mode="json"),
        "report_aliases": v1_models.ReportCreate.model_validate(
            {
                "question_id": "q_123",
                "title": "Alias report",
                "summary_markdown": "# Alias report",
                "focus_label": "Alias behavior",
                "finding_id": "clm_123",
            }
        ).model_dump(mode="json"),
        "permissive_selector": v1_models.SourceSelector.model_validate(
            {
                "type": "LegacySelector",
                "exact": "legacy quote",
                "vendor_extension": {"line": 1},
            }
        ).model_dump(mode="json"),
    }


def test_v1_model_imports_are_identity_compatible() -> None:
    exported_names = (
        "QuestionCreate",
        "ResearchSessionCreate",
        "SourceCreate",
        "SourceSelector",
        "ExcerptCreate",
        "ClaimCreate",
        "ReportCreate",
        "AnnotationCreate",
        "FindingCreate",
    )

    for name in exported_names:
        assert getattr(v1, name) is getattr(v1_models, name)


def test_v1_alias_and_dump_behavior_is_stable() -> None:
    expected = _load_json(CONTRACTS / "v1_model_compatibility.json")

    assert _v1_characterization() == expected


@pytest.mark.parametrize(
    ("filename", "model", "path"),
    [
        ("deposit-bundle.json", ResearchDepositRequest, ()),
        ("deposit-receipt.json", ResearchDepositResult, ()),
        ("search-request.json", ResearchSearchRequest, ()),
        ("search-response.json", ResearchSearchResponse, ()),
        ("review-request.json", ResearchReviewRequest, ()),
        ("refresh-request.json", ResearchRefreshRequest, ()),
        ("git-evidence.json", DepositSource, ("source",)),
        ("git-evidence.json", DepositEvidence, ("evidence",)),
        ("web-evidence.json", DepositSource, ("source",)),
        ("web-evidence.json", DepositEvidence, ("evidence",)),
    ],
)
def test_packet_examples_are_golden_valid(
    filename: str,
    model: type[BaseModel],
    path: tuple[str, ...],
) -> None:
    payload = _load_json(V2_CONTRACTS / "valid" / "packet" / filename)
    for key in path:
        payload = payload[key]

    validated = model.model_validate(payload)

    assert validated.model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("status-response.json", ResearchStatusResponse),
        ("get-request.json", ResearchGetRequest),
        ("error-response.json", ResearchErrorResponse),
        ("minimal-deposit.json", ResearchDepositRequest),
    ],
)
def test_additional_v2_examples_are_golden_valid(
    filename: str,
    model: type[BaseModel],
) -> None:
    payload = _load_json(V2_CONTRACTS / "valid" / filename)

    assert model.model_validate(payload).model_dump(mode="json", exclude_none=True)


def test_invalid_vectors_fail_for_the_intended_reason() -> None:
    models: dict[str, type[BaseModel]] = {
        "deposit": ResearchDepositRequest,
        "error": ResearchErrorResponse,
        "get": ResearchGetRequest,
        "refresh": ResearchRefreshRequest,
        "review": ResearchReviewRequest,
        "search": ResearchSearchRequest,
    }
    vectors = _load_json(V2_CONTRACTS / "invalid" / "vectors.json")

    for vector in vectors:
        payload = vector["payload"]
        if repeat := vector.get("repeat"):
            payload[repeat["field"]] *= repeat["count"]
        with pytest.raises(ValidationError) as exc_info:
            models[vector["model"]].model_validate(payload)
        errors = exc_info.value.errors(include_url=False)

        assert vector["error_type"] in {error["type"] for error in errors}, vector["name"]
        if field := vector.get("field"):
            rendered_locations = [
                ".".join(str(part) for part in error["loc"]) for error in errors
            ]
            assert any(field in location for location in rendered_locations), vector["name"]
        if message := vector.get("message_contains"):
            assert any(message in error["msg"] for error in errors), vector["name"]


def test_v2_provenance_defaults_to_unknown_not_a_named_model() -> None:
    payload = _load_json(V2_CONTRACTS / "valid" / "minimal-deposit.json")

    request = ResearchDepositRequest.model_validate(payload)

    assert request.run.provenance.provider is None
    assert request.run.provenance.model is None
    assert request.run.provenance.model_version is None


def test_v2_schema_snapshots_are_deterministic() -> None:
    models: dict[str, type[BaseModel] | TypeAdapter[Any]] = {
        "research-deposit-result-v2.schema.json": ResearchDepositResult,
        "research-deposit-v2.schema.json": ResearchDepositRequest,
        "research-error-v2.schema.json": ResearchErrorResponse,
        "research-get-v2.schema.json": ResearchGetRequest,
        "research-refresh-v2.schema.json": ResearchRefreshRequest,
        "research-review-v2.schema.json": ResearchReviewRequest,
        "research-search-response-v2.schema.json": ResearchSearchResponse,
        "research-search-v2.schema.json": ResearchSearchRequest,
        "research-status-response-v2.schema.json": ResearchStatusResponse,
        "source-selector-v2.schema.json": TypeAdapter(SourceSelectorV2),
    }

    for filename, model in models.items():
        current = (
            model.json_schema()
            if isinstance(model, TypeAdapter)
            else model.model_json_schema()
        )
        expected = _load_json(V2_CONTRACTS / "schemas" / filename)

        assert current == expected, filename
