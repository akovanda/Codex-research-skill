# Changelog

## v0.1.0

Initial open-source preview release.

Highlights:

- question-led research model with question, session, source, excerpt, claim, and report records
- FastAPI app plus JSON API
- HTTP MCP endpoint with localhost-first Codex integration
- managed localhost runtime for multiple local Codex instances
- shared self-hosted Compose deployment for internal teams
- package-ready `research-registry` CLI with `up`, `doctor`, `repair`, `status`, `token`, `down`, and `uninstall`
- packaged Codex skills for repo-free installs through `uvx` or future `pipx`
- source-backed research capture and memory/retrieval skills
- import, brief, refresh, and follow-up workflow endpoints for reuse-first research iteration
- `make workflow-check` for live HTTP plus harness validation
- onboarding docs, FAQ, and issue templates for the preview release

Known limits:

- internal-only shared deployment support
- Kubernetes is example-only in this preview
- PyPI and Homebrew packaging are prepared but not yet the primary tagged release channel
- local runtime installs depend on a published or overridden container image outside source-checkout builds
