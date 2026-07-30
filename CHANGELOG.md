# Changelog

## v0.1.0

Initial open-source preview release.

Highlights:

- question-led research model with question, session, source, excerpt, claim, and report records
- FastAPI app plus JSON API
- personal SQLite/content-addressed blob storage with tokenless STDIO MCP
- idempotent XDG initialization, doctor, verified backup, and optional review server
- HTTP MCP endpoint with authenticated shared Codex integration
- retained managed Docker/Postgres runtime for shared local use
- shared self-hosted Compose deployment for internal teams
- package-ready `research-registry` CLI with personal `init`, `doctor`,
  `backup`, `serve`, and `mcp` plus retained shared-runtime commands
- packaged Codex skills for repo-free installs through `uvx` or future `pipx`
- source-backed research capture and memory/retrieval skills
- import, brief, refresh, and follow-up workflow endpoints for reuse-first research iteration
- `make workflow-check` for live HTTP plus harness validation
- onboarding docs, FAQ, and issue templates for the preview release

Known limits:

- internal-only shared deployment support
- Kubernetes is example-only in this preview
- PyPI and Homebrew packaging are prepared but not yet the primary tagged release channel
- Docker/Compose and a published or overridden image are required only for
  shared container deployments
