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

- local STDIO MCP for personal use
- FastAPI web app and JSON API for optional review/shared use
- authenticated HTTP MCP for shared deployments

Secondary integrations:

- focused Codex recall and explicit deposit skills
- explicit-only legacy heuristic adapters, harnesses, and pass runners

## Deployment Modes

### Local default

- SQLite under the XDG data directory
- content-addressed filesystem blobs under the same private data root
- tokenless same-user STDIO MCP
- additive first-run migrations
- no daemon, system service, Docker, Postgres, or network listener required
- optional authenticated loopback review server

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

The application contracts accept either:

- a local SQLite path
- a `sqlite:///...` URL
- a Postgres URL

Personal deployments use SQLite. Shared/team deployments use Postgres. Both
dialects retain v1 tables and apply the same logical additive migrations.
Database files and dumps are not cross-dialect interchange formats.

Large immutable source content is stored outside the database using generated
SHA-256 keys. Database rows hold hashes, sizes, media types, and storage keys.
Blob roots are private dedicated directories; API/MCP callers never supply a
filesystem path.

## Compatibility

Canonical terms are:

- question
- session
- excerpt
- claim
- report

Compatibility aliases such as `annotation` and `finding` remain available for older clients, but new integrations should not depend on them as the primary model.

Normal startup does not import the legacy local-research, repository
intelligence, broad-capture, or specialist-routing modules. See
[Implicit research capture](implicit-research-capture.md) for the explicit
compatibility flags and removal criteria.
