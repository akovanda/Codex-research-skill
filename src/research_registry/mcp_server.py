from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .backend_client import create_backend
from .config import load_settings
from .models import ClaimCreate, ExcerptCreate, PublishRequest, QuestionCreate, ReportCreate, ResearchSessionCreate, SourceCreate

settings = load_settings()
service = create_backend(settings)

mcp = FastMCP(
    "Research Registry",
    instructions="Question-led research memory with excerpt-backed evidence, reusable claims, reports, and publication controls.",
    json_response=True,
)


@mcp.tool()
def search(query: str, kind: str | None = None, include_private: bool = True, limit: int = 10) -> dict:
    """Search questions, excerpts, claims, reports, and sources."""
    return service.search(query, kind=kind, include_private=include_private, limit=limit).model_dump(mode="json")


@mcp.tool()
def backend_status() -> dict:
    """Return the selected backend URL, namespace, and selection source."""
    return service.backend_status().model_dump(mode="json")


@mcp.tool()
def create_question(payload: dict) -> dict:
    """Create or reuse a research question and its focus label."""
    question = service.create_question(QuestionCreate.model_validate(payload))
    return question.model_dump(mode="json")


@mcp.tool()
def create_session(payload: dict) -> dict:
    """Create a research session for a question."""
    session = service.create_session(ResearchSessionCreate.model_validate(payload))
    return session.model_dump(mode="json")


@mcp.tool()
def get_question(question_id: str, include_private: bool = True) -> dict:
    """Fetch a single question by id."""
    return service.get_question(question_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_source(source_id: str, include_private: bool = True) -> dict:
    """Fetch a single source by id."""
    return service.get_source(source_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_excerpt(excerpt_id: str, include_private: bool = True) -> dict:
    """Fetch a single excerpt by id."""
    return service.get_excerpt(excerpt_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_annotation(annotation_id: str, include_private: bool = True) -> dict:
    """Compatibility alias for fetching an excerpt by id."""
    return service.get_excerpt(annotation_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_claim(claim_id: str, include_private: bool = True) -> dict:
    """Fetch a single claim by id."""
    return service.get_claim(claim_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_finding(finding_id: str, include_private: bool = True) -> dict:
    """Compatibility alias for fetching a claim by id."""
    return service.get_claim(finding_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def get_report(report_id: str, include_private: bool = True) -> dict:
    """Fetch a single report by id."""
    return service.get_report(report_id, include_private=include_private).model_dump(mode="json")


@mcp.tool()
def create_source(payload: dict) -> dict:
    """Create or reuse a source record."""
    source = service.create_source(SourceCreate.model_validate(payload))
    return source.model_dump(mode="json")


@mcp.tool()
def add_excerpt(payload: dict) -> dict:
    """Create a source-backed evidence excerpt."""
    excerpt = service.create_excerpt(ExcerptCreate.model_validate(payload))
    return excerpt.model_dump(mode="json")


@mcp.tool()
def add_annotation(payload: dict) -> dict:
    """Compatibility alias for creating an evidence excerpt."""
    excerpt = service.create_excerpt(ExcerptCreate.model_validate(payload))
    return excerpt.model_dump(mode="json")


@mcp.tool()
def create_claim(payload: dict) -> dict:
    """Create a claim from one or more excerpt ids."""
    claim = service.create_claim(ClaimCreate.model_validate(payload))
    return claim.model_dump(mode="json")


@mcp.tool()
def create_finding(payload: dict) -> dict:
    """Compatibility alias for creating a claim from excerpt ids."""
    claim = service.create_claim(ClaimCreate.model_validate(payload))
    return claim.model_dump(mode="json")


@mcp.tool()
def create_report(payload: dict) -> dict:
    """Create a report with explicit summary markdown from one or more claim ids."""
    report = service.create_report(ReportCreate.model_validate(payload))
    return report.model_dump(mode="json")


@mcp.tool()
def publish(kind: str, record_id: str, cascade_linked_sources: bool = True) -> dict:
    """Publish a source, question, excerpt, claim, or report."""
    service.publish(PublishRequest(kind=kind, record_id=record_id, cascade_linked_sources=cascade_linked_sources))
    return {"status": "ok", "kind": kind, "record_id": record_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
