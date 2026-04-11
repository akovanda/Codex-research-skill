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
- HTTP MCP endpoint and compatibility stdio MCP server

Secondary integrations:

- Codex skills for implicit and domain-specific capture
- local harnesses and pass runners

## Deployment Modes

### Local default

- recommended release path: managed localhost runtime on `127.0.0.1:8010`
- FastAPI app plus HTTP MCP
- Postgres in local Docker Compose
- one shared local backend for multiple Codex instances
- repo-local SQLite remains available as a developer-only compatibility path

### Shared self-hosted

- FastAPI app behind normal internal networking
- Postgres
- API keys plus admin token
- one or more org/user namespaces
- release support is internal-only, not direct public-internet exposure

### Kubernetes

- manifests are example assets for teams that already run clusters
- Kubernetes is not a release-critical or production-hardened path in this preview

The current preview does not target a public multi-tenant shared service.

## Storage

The service accepts either:

- a local SQLite path
- a `sqlite:///...` URL
- a Postgres URL

Managed localhost and shared deployments should use Postgres. SQLite remains available for repo-local development and compatibility workflows.

## Compatibility

Canonical terms are:

- question
- session
- excerpt
- claim
- report

Compatibility aliases such as `annotation` and `finding` remain available for older clients, but new integrations should not depend on them as the primary model.
