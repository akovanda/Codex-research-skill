# Changelog

## Unreleased

Planned package and plugin version: `0.2.0a2`. This draft does not publish,
tag, or release that version.

- Made validate-only and commit forms of the same v2 deposit share one stable
  request hash, allowing clients to prove that the validated payload is the
  payload they committed without changing existing committed receipt hashes.
- Persisted shared-Compose content-addressed blobs on a dedicated app data
  volume and covered container recreation in the shared deployment smoke.

- Added deterministic retrieval metrics (Recall@k, MRR, nDCG, Precision@5,
  evidence/state accuracy, duplicates, no-answer accuracy, latency, bytes, and
  calls), operator-local known-answer evaluation, and a recorded four-mode
  native-memory/registry comparison without an agent loop.
- Added content-free evidence/deposit/migration health metrics, expanded
  security/fuzz/private-data/log scanning, copied-data SQLite
  install/upgrade/backup/restore/rollback rehearsal, and fixed
  alpha/beta/stable release gates.
- Pinned GitHub Actions to immutable commits, made release workflows
  manual-only, and added offline checksums, SPDX SBOM, and unsigned provenance
  generation. No package, image, tag, or artifact is automatically published.
- Published current alpha limitations, v1 compatibility/deprecation policy,
  and operator upgrade/rollback instructions.
- Isolated local heuristic research, repository intelligence, specialist
  routing, and broad capture behind
  `RESEARCH_REGISTRY_LEGACY_HEURISTICS=1`.
- Disabled low-level v1 MCP tools by default while preserving them behind
  `RESEARCH_REGISTRY_MCP_LEGACY=1`; v1 HTTP and database compatibility remain.
- Kept the capture queue, including atomic v2 deposit envelopes, and marked
  legacy regression suites explicitly.
- Classified PostgreSQL deposit failures by SQLSTATE so only serialization,
  deadlock, and lock-contention failures are reported as retryable concurrency;
  uniqueness, integrity, schema, availability, cancellation, resource,
  transaction-state, and internal failures retain distinct content-free codes.
- Hardened server and client trust boundaries: tokenless administration is now
  limited to explicit loopback exposure, remote backends require HTTPS,
  readiness failures no longer echo storage details, question status writes
  reject unknown states, and HTTPS admin sessions use Secure cookies.

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
