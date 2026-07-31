# Deprecation Policy

Research Registry uses semantic package versions and explicit MCP protocol
versions.

- V2 alpha keeps v1 HTTP and MCP compatibility available. Broad implicit
  capture and heuristic research paths are deprecated.
- V2 beta makes the focused v2 plugin and docs primary. Legacy low-level MCP is
  opt-in with `RESEARCH_REGISTRY_MCP_LEGACY=1`; v1 HTTP remains supported.
- V2 stable keeps v1 HTTP deprecated but supported for the published
  compatibility period. V1 tables and data remain.
- Removal requires a future major-release ADR, published notice, and a tested
  migration/export path.

A deprecation warning identifies the capability, replacement, first deprecated
version, earliest removal version or “not scheduled,” and documentation path.
Warnings should not repeat within one process where practical.

Current deprecated paths are the broad capture classifier, local heuristic
research, repository intelligence, specialist routing, and low-level v1 MCP
tools. Their replacements are the focused `research-recall` and
`research-deposit` skills plus the high-level v2 status/search/get/deposit/
review/refresh tools. Removal is not scheduled.

The retained `sources.review_state` and `sources.conflict_state` columns are
v1 compatibility mirrors, not authoritative state for immutable v2 source
versions. The mirror follows the latest source version's effective decision
and resets to unreviewed/non-conflicted when a newer native version is stored.
Each v2 source version derives its own state only from review events for that
exact version, falling back to unreviewed when no decision exists.
