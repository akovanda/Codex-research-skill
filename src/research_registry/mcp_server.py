from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .backend_client import create_backend
from .config import load_settings
from .models import (
    AnnotationCreate,
    FindingCreate,
    PublishRequest,
    ReportCreate,
    ReportCompileCreate,
    RunCreate,
)

settings = load_settings()
service = create_backend(settings)

mcp = FastMCP(
    "Research Registry",
    instructions="Source-backed research memory for annotations, findings, reports, and publishing flows.",
    json_response=True,
)


@mcp.tool()
def search(query: str, kind: str | None = None, include_private: bool = True, limit: int = 10) -> dict:
    """Search annotations, findings, reports, and sources."""
    return service.search(query, kind=kind, include_private=include_private, limit=limit).model_dump(mode="json")


@mcp.tool()
def backend_status() -> dict:
    """Return the selected backend URL, namespace, and selection source."""
    return service.backend_status().model_dump(mode="json")


@mcp.tool()
def create_run(payload: dict) -> dict:
    """Create a research run for provenance and grouping."""
    run = service.create_run(RunCreate.model_validate(payload))
    return run.model_dump(mode="json")


@mcp.tool()
def get_source(source_id: str, include_private: bool = True) -> dict:
    """Fetch a single source by id."""
    return service.get_source(source_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_annotation(annotation_id: str, include_private: bool = True) -> dict:
    """Fetch a single annotation by id."""
    return service.get_annotation(annotation_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_finding(finding_id: str, include_private: bool = True) -> dict:
    """Fetch a single finding by id."""
    return service.get_finding(finding_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_report(report_id: str, include_private: bool = True) -> dict:
    """Fetch a single report by id."""
    return service.get_report(report_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def add_annotation(payload: dict) -> dict:
    """Create a source-anchored annotation. The payload matches the HTTP AnnotationCreate schema."""
    annotation = service.create_annotation(AnnotationCreate.model_validate(payload))
    return annotation.model_dump(mode="json")


@mcp.tool()
def create_finding(payload: dict) -> dict:
    """Create a finding from one or more annotation ids."""
    finding = service.create_finding(FindingCreate.model_validate(payload))
    return finding.model_dump(mode="json")


@mcp.tool()
def create_report(payload: dict) -> dict:
    """Create a report with explicit summary markdown from one or more finding ids."""
    report = service.create_report(ReportCreate.model_validate(payload))
    return report.model_dump(mode="json")


@mcp.tool()
def compile_report(payload: dict) -> dict:
    """Create a report from one or more finding ids."""
    report = service.compile_report(ReportCompileCreate.model_validate(payload))
    return report.model_dump(mode="json")


@mcp.tool()
def publish(kind: str, record_id: str, cascade_linked_sources: bool = True) -> dict:
    """Publish a source, annotation, finding, or report."""
    service.publish(PublishRequest(kind=kind, record_id=record_id, cascade_linked_sources=cascade_linked_sources))
    return {"status": "ok", "kind": kind, "record_id": record_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
