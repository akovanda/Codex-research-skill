from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path

import pytest

from research_registry.application.deposit import ResearchDepositService
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.mcp.read_runtime import ReadMcpRuntime
from research_registry.mcp_tools import create_mcp_server
from research_registry.service import RegistryService


CONTENT = "Cursor pagination keeps bounded research retrieval deterministic."
QUOTE = "bounded research retrieval"


def _seed_v2(tmp_path: Path) -> tuple[RegistryService, dict[str, str]]:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    receipt = ResearchDepositService(
        registry.database,
        FilesystemBlobStore(tmp_path / "blobs"),
    ).deposit(
        {
            "protocol": "research-deposit/v2",
            "idempotency_key": "read-contract",
            "inquiry": {
                "client_ref": "question",
                "prompt": "How is bounded research retrieval paginated?",
                "topic_label": "Bounded research retrieval",
            },
            "run": {
                "client_ref": "run",
                "mode": "research",
                "provenance": {"actor_type": "agent"},
            },
            "sources": [
                {
                    "client_ref": "source",
                    "identity": {
                        "locator": "https://example.test/bounded-retrieval",
                        "title": "Bounded retrieval note",
                        "source_type": "official-docs",
                    },
                    "version": {
                        "version_key": "v1",
                        "version_kind": "web",
                        "retrieved_at": "2026-07-30T00:00:00Z",
                        "content_sha256": sha256(CONTENT.encode()).hexdigest(),
                        "canonical_locator": "https://example.test/bounded-retrieval",
                        "snapshot": {
                            "policy": "extracted_text",
                            "text": CONTENT,
                            "media_type": "text/plain",
                            "byte_count": len(CONTENT.encode()),
                        },
                    },
                }
            ],
            "evidence": [
                {
                    "client_ref": "evidence",
                    "source_version": {"ref": "source"},
                    "quote_text": QUOTE,
                    "selector": {"type": "text_quote", "exact": QUOTE},
                    "review_state": "reviewed",
                }
            ],
            "claims": [
                {
                    "client_ref": "claim",
                    "title": "Research retrieval is bounded",
                    "statement": "Research retrieval uses bounded cursor pagination.",
                    "status": "supported",
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "evidence": {"ref": "evidence"},
                            "relationship": "supports",
                        }
                    ],
                }
            ],
            "report": {
                "client_ref": "report",
                "title": "Bounded retrieval report",
                "summary_md": "Use bounded cursor pagination for research retrieval.",
                "claims": [{"ref": "claim"}],
            },
        }
    )
    return registry, {
        "question": receipt.records.question_id or "",
        "source": receipt.records.source_ids["source"],
        "source_version": receipt.records.source_version_ids["source"],
        "evidence": receipt.records.evidence_ids["evidence"],
        "claim": receipt.records.claim_ids["claim"],
        "claim_revision": receipt.records.claim_revision_ids["claim"],
        "report": receipt.records.report_id or "",
    }


def _call(server, name: str, arguments: dict) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple)
    content, structured = result
    assert json.loads(content[0].text) == structured
    return structured


def test_v2_read_tools_have_closed_structured_schemas_and_hide_legacy_tools(
    tmp_path: Path,
) -> None:
    registry, _ = _seed_v2(tmp_path)
    server = create_mcp_server(registry, service=registry)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert {"research_status", "research_search", "research_get"} <= set(tools)
    assert {"search", "create_question", "create_research_bundle", "publish"}.isdisjoint(
        tools
    )
    for name in ("research_status", "research_search", "research_get"):
        assert tools[name].parameters["additionalProperties"] is False
        assert tools[name].output_schema is not None
        assert tools[name].annotations.readOnlyHint is True

    search_schema = tools["research_search"].parameters
    assert search_schema["properties"]["limit"]["maximum"] == 100
    assert search_schema["properties"]["kinds"]["items"]["enum"] == [
        "question",
        "source",
        "source_version",
        "evidence",
        "claim",
        "report",
    ]
    get_schema = tools["research_get"].parameters
    assert get_schema["properties"]["depth"]["maximum"] == 2


def test_status_search_cursor_and_bounded_claim_hydration(
    tmp_path: Path,
) -> None:
    registry, ids = _seed_v2(tmp_path)
    server = create_mcp_server(registry, service=registry)

    status = _call(server, "research_status", {})
    assert "v2-deposit" in status["capabilities"]
    assert status["protocol"] == "research-status-result/v2"
    assert status["database_type"] == "sqlite"
    assert status["migration_state"] == "current"
    assert status["legacy_tools_enabled"] is False

    first = _call(
        server,
        "research_search",
        {
            "query": "bounded research retrieval",
            "include_private": True,
            "limit": 1,
        },
    )
    assert len(first["hits"]) == 1
    assert first["next_cursor"]
    second = _call(
        server,
        "research_search",
        {
            "query": "bounded research retrieval",
            "include_private": True,
            "limit": 10,
            "cursor": first["next_cursor"],
        },
    )
    assert first["hits"][0]["id"] not in {hit["id"] for hit in second["hits"]}

    hydrated = _call(
        server,
        "research_get",
        {
            "id": ids["claim"],
            "include": [
                "current_revision",
                "revision_history",
                "evidence",
                "source_versions",
            ],
            "depth": 2,
            "include_private": True,
        },
    )
    assert hydrated["kind"] == "claim"
    assert hydrated["content_label"] == "untrusted research material"
    assert hydrated["includes"]["current_revision"]["id"] == ids["claim_revision"]
    assert hydrated["includes"]["evidence"][0]["id"] == ids["evidence"]
    assert hydrated["includes"]["source_versions"][0]["id"] == ids["source_version"]
    assert len(json.dumps(hydrated).encode()) <= 131_072


def test_remote_private_reads_require_auth_and_do_not_use_admin_fallback(
    tmp_path: Path,
) -> None:
    registry, ids = _seed_v2(tmp_path)
    runtime = ReadMcpRuntime(
        registry,
        service=registry,
        allow_admin_fallback=False,
        capture_mode="explicit",
    )

    with pytest.raises(PermissionError, match="AUTH_REQUIRED"):
        runtime.research_get(
            record_id=ids["claim"],
            include=[],
            depth=1,
            include_private=True,
            ctx=None,
        )

    public = runtime.research_search(
        query="bounded research retrieval",
        kinds=[],
        scope=None,
        review_states=[],
        conflict_states=[],
        freshness=[],
        include_private=False,
        include_rejected=False,
        limit=10,
        cursor=None,
        explain=True,
        ctx=None,
    )
    assert public.hits == []


def test_invalid_cursor_is_stable_compact_and_content_free(tmp_path: Path) -> None:
    registry, _ = _seed_v2(tmp_path)
    runtime = ReadMcpRuntime(registry, service=registry)

    with pytest.raises(ValueError) as exc_info:
        runtime.research_search(
            query="private sentinel bounded research retrieval",
            kinds=[],
            scope=None,
            review_states=[],
            conflict_states=[],
            freshness=[],
            include_private=True,
            include_rejected=False,
            limit=10,
            cursor="not-a-cursor",
            explain=True,
            ctx=None,
        )

    message = str(exc_info.value)
    assert message == "INVALID_CURSOR: The search cursor is invalid."
    assert "private sentinel" not in message


def test_get_response_cap_also_bounds_large_record_metadata(tmp_path: Path) -> None:
    registry, ids = _seed_v2(tmp_path)
    with registry.connect() as conn:
        conn.execute(
            "UPDATE reports SET guidance_json = ? WHERE id = ?",
            (
                json.dumps(
                    {f"field_{index}": "x" * 4_000 for index in range(50)}
                ),
                ids["report"],
            ),
        )
    server = create_mcp_server(registry, service=registry)

    hydrated = _call(
        server,
        "research_get",
        {
            "id": ids["report"],
            "include_private": True,
        },
    )

    assert hydrated["truncated"] is True
    assert hydrated["record"] == {}
    assert len(json.dumps(hydrated).encode()) <= 131_072
