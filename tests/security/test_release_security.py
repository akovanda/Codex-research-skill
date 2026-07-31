from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_registry.contracts.v2 import ResearchSearchRequest
from research_registry.release.security import (
    SensitiveLogFinding,
    scan_log_text,
)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "x" * 10_001,
        "\ud800",
    ],
)
def test_search_contract_fuzz_rejects_invalid_or_unencodable_queries(
    query: str,
) -> None:
    with pytest.raises((ValidationError, UnicodeEncodeError)):
        request = ResearchSearchRequest(
            protocol="research-search/v2",
            query=query,
        )
        request.model_dump_json().encode("utf-8")


def test_sensitive_log_scanner_finds_credentials_and_content_markers() -> None:
    forbidden = [
        "private prompt sentinel",
        "private source body sentinel",
        "private evidence quote sentinel",
    ]
    clean = json.dumps(
        {
            "request_id": "req_123",
            "operation": "research_search",
            "duration_ms": 12,
            "result_count": 2,
            "status": "ok",
        }
    )
    assert scan_log_text(clean, forbidden_values=forbidden) == ()

    leaked = (
        clean
        + "\nAuthorization: Bearer secret-token"
        + "\nprivate evidence quote sentinel"
    )
    findings = scan_log_text(leaked, forbidden_values=forbidden)
    assert {finding.kind for finding in findings} == {
        "credential_pattern",
        "forbidden_value",
    }
    assert all(isinstance(finding, SensitiveLogFinding) for finding in findings)
    assert all("secret-token" not in repr(finding) for finding in findings)


def test_sensitive_log_scanner_reads_bounded_files(tmp_path: Path) -> None:
    path = tmp_path / "registry.log"
    path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="5 MiB"):
        scan_log_text(path)
