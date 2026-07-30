# Security

If you believe you have found a security issue in Research Registry, do not open a public issue with exploit details.

Research Registry is currently a developer preview. Shared deployments are expected to stay on private networks behind normal operator controls. That does not remove the need for responsible disclosure.

## Preferred Reporting Path

Use GitHub's private vulnerability reporting flow for this repository:

1. Open the repository on GitHub.
2. Go to the `Security` tab.
3. Use `Report a vulnerability`.

If private vulnerability reporting is not enabled for the repository yet, do not post exploit details publicly. Enable that GitHub feature before advertising a broader public preview, or replace this document with another maintainer-owned private reporting path.

## What To Include

Please include:

- affected version or commit
- impact
- reproduction steps
- any suggested mitigations

Helpful extras:

- whether the issue affects localhost-only installs, shared Compose deployments, or both
- whether the issue requires authenticated access
- whether the issue depends on operator misconfiguration
- logs, screenshots, or request/response traces that make the issue easier to reproduce

## Scope Notes

- Preview support is strongest for the managed localhost runtime and the shared self-hosted Compose deployment.
- Kubernetes manifests are example deployment assets in this preview, not a production-hardening claim.
- Public-internet exposure is not a supported default operating mode for this release.

## Audit and backup tooling

The `audit-data`, `backup`, and `restore` commands are local operator tools.
They do not add an HTTP or MCP surface.

- SQLite audit connections use read-only URI mode plus `PRAGMA query_only`.
- Audit reports contain aggregate counts and health states, not stored content
  samples, URL query strings, or credentials.
- SQLite online backups and manifests are mode `0600` on supported POSIX
  systems and refuse existing destinations.
- Restore verification checks artifact SHA-256, SQLite integrity, foreign keys,
  row counts, and deterministic table hashes before accepting the copy.
- Postgres planning drops usernames, passwords, and URL query strings and
  returns argv arrays rather than executable shell text.
- V1 manifests explicitly report that no v2 blob store is configured; they do
  not imply that future blob content has been backed up.

Treat audit reports, backup artifacts, and manifests as sensitive even though
default reports omit content. Keep them under normal host access controls.

## Disclosure Expectations

- Do not publish exploit details before the maintainer has had a reasonable chance to reproduce and mitigate the issue.
- If you are unsure whether something is security-sensitive, report it privately first.
- Public bug reports without exploit details are still fine for clearly non-security defects.

## Maintainer Action Before Tagging

Before tagging or advertising a broader public preview:

- confirm GitHub private vulnerability reporting is enabled for the repository
- or replace the preferred reporting path above with another maintainer-owned private channel you actually monitor
