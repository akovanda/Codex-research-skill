# Architecture

Research Registry is built around a question-led research model:

- `Question` defines the thing being investigated.
- `ResearchSession` records one research pass and its freshness window.
- `Source` captures the underlying document or artifact.
- `Excerpt` stores source-backed evidence.
- `Claim` turns excerpts into reusable propositions.
- `Report` stores guidance on top of claims, gaps, needs, wants, and linked follow-up questions.

## Product Shape

Primary surfaces:

- FastAPI web app
- JSON API
- MCP server

Secondary integrations:

- Codex skills for implicit and domain-specific capture
- local harnesses and pass runners

## Deployment Modes

### Local default

- embedded backend
- SQLite
- localhost routing
- no external dependency

### Shared self-hosted

- FastAPI app behind normal internal networking
- Postgres
- API keys plus admin token
- one or more org/user namespaces

The current preview does not target a public multi-tenant shared service.

## Storage

The service accepts either:

- a local SQLite path
- a `sqlite:///...` URL
- a Postgres URL

Local default behavior still uses SQLite. Shared deployments should use Postgres.

## Compatibility

Canonical terms are:

- question
- session
- excerpt
- claim
- report

Compatibility aliases such as `annotation` and `finding` remain available for older clients, but new integrations should not depend on them as the primary model.
