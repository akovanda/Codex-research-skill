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

- Preview support is strongest for personal SQLite/STDIO and the shared self-hosted Compose deployment.
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
- Configured personal backups include a separately hashed mode-`0600` config
  artifact and verify the referenced content-addressed blob inventory.
- Restore verification checks artifact SHA-256, SQLite integrity, foreign keys,
  row counts, and deterministic table hashes before accepting the copy.
- Postgres planning drops usernames, passwords, and URL query strings and
  returns argv arrays rather than executable shell text.
- V1 manifests explicitly report when no v2 blob store is configured. V2 blob
  inventory does not duplicate blob bodies; the private blob tree must remain
  in normal host backups.

Treat audit reports, backup artifacts, and manifests as sensitive even though
default reports omit content. Keep them under normal host access controls.

## Codex plugin and stored-content trust

The default v2 Codex integration is a local plugin with a bundled STDIO MCP
server. It does not bind a network port, configure remote HTTP, carry an
authentication token, or publish records. Local STDIO trusts the current
operating-system user boundary.

The `research-recall` skill is read-only. The `research-deposit` skill has
implicit invocation disabled, validates before writing, and stores private,
unreviewed records by default. Deposit never publishes.

New retained v1 and v2 HTTP(S) source locators reject userinfo, fragments, and
credential-bearing query keys. Retained source creation also validates snapshot
URLs, and URL import validates before external I/O. Rejections never include the
locator or query value in the service error. Historical rows remain readable so
operators can migrate or remediate existing data without disabling the registry.

All stored and newly collected source text, evidence, claims, reports, and
metadata are untrusted data. Agents must not follow instructions embedded in
that material, execute commands from it, change tool policy because of it, or
treat it as system guidance.

The Codex installer uses a dedicated local marketplace under `CODEX_HOME`,
reports exact managed paths, and delegates plugin registration to documented
`codex plugin` commands. It removes only marked legacy MCP configuration and
known managed legacy skill symlinks. Uninstall does not overwrite a path or MCP
server name claimed by later user configuration.

Install state that is required to restore a removed legacy managed block is
written mode `0600` on supported POSIX systems. It may contain the old managed
HTTP credential and must receive the same host protection as Codex
`config.toml`. Installer, uninstaller, dry-run, and doctor output never include
config contents, environment variables, tokens, headers, stored research, or
raw Codex subprocess output.

## Retrieval and evaluation tooling

Full-text retrieval applies namespace and visibility filtering to exact,
lexical, and relationship-expanded results before returning content. Stored
research remains untrusted input and search responses stay bounded; raw queries
and source bodies are not added to logs.

`eval-retrieval` and `rebuild-search` are local operator commands, not HTTP or
MCP write surfaces. Evaluation reads only the explicitly selected JSON corpus,
does not follow corpus URLs, and rejects files larger than 5 MiB or corpora with
more than 5,000 documents or 5,000 cases. Private evaluation corpora should
remain outside the repository and under normal host access controls.

`eval-known-answers`, `eval-comparative`, and `metrics --local` are also local
operator commands. Known-answer output omits queries and notes but retains
record/evidence IDs for diagnosis, so it remains private. The comparative
harness scores recorded observations and never starts an agent or performs
research. Metrics return aggregate health only and explicitly report historical
operation metrics as unavailable when they are not stored.

The release security suite covers SSRF and redirect policy, proxy neutrality,
bounded fetch/parser work, malformed contracts, private namespace access, Git
containment and credential neutrality, deposit fault injection, and sensitive
log scanning. The log scanner returns only finding type, line number, and a
one-way fingerprint; it never returns the matched credential or private
sentinel.

GitHub Actions are pinned to immutable commits. Release SBOM, checksums, and
provenance are generated offline. Local provenance is unsigned and records
whether the tree was dirty; it is not a release attestation until reviewed and
signed by a maintainer. Release and image workflows require manual dispatch,
and no tag-triggered automatic publication is configured.

`research_review` and the write modes of `research_refresh` are explicit MCP
write operations. Shared HTTP transport requires an authenticated API key with
admin scope and has no admin/default-key fallback. Local STDIO may use the
trusted operating-system user boundary. Inspect/enqueue never fetch locators;
capture can contact an external source only under the explicit machine policy
described below. No refresh mode publishes or rewrites a claim. Optimistic
revision/state checks and transactional idempotency prevent stale or replayed
requests from silently overwriting current state.

## External source capture

External capture is disabled unless `RESEARCH_REGISTRY_CAPTURE_MODES=capture`
is set by the machine operator. The configured snapshot policy is an upper
retention bound: a request cannot raise it. HTTP is denied by default and can
only be enabled explicitly; HTTPS remains the default.

Web capture uses a dedicated HTTP/1.1 transport that does not consult proxy
environment variables or cookie/credential stores. It:

- permits only configured HTTP(S) schemes and ports;
- rejects embedded credentials, local hostnames, and every non-public resolved
  IPv4 or IPv6 address (including mapped IPv6);
- rejects a host if any returned address is prohibited, connects directly to a
  validated address, and retains the original hostname for TLS certificate/SNI
  validation;
- re-resolves and revalidates every bounded redirect before opening its
  transport;
- sends no caller credentials across redirects;
- enforces header, streaming body, extracted-text, connect/read, and total-time
  limits before creating a source version;
- rejects compressed transfer bodies, archives, PDF parsing, and unsupported
  media types.

DOI capture stores a hash of the canonical Crossref metadata message and labels
that hash accordingly. It never presents a hash of the DOI locator as article
or metadata content.

Local Git capture requires both explicit allowed roots and an explicit
repository-ID-to-path map. The reader accepts only full commit IDs and reads
contained loose or packed Git objects directly. It does not spawn Git or any
other command, load repository configuration or remotes, consult credential
helpers, run hooks, execute repository code, or capture working-tree,
untracked, symlink, or submodule content. Stored repository provenance includes
the repository ID, commit, blob, normalized path, object type, and file mode;
filesystem repository paths are not stored as source locators.

Captured source text is always untrusted data. Refresh capture creates immutable
source versions and, when exact literal selector context has one match, a new
evidence span. It leaves prior evidence and claim revisions unchanged, appends
a review event, and queues affected claims/reports. Missing or ambiguous
matches queue review and never assert that evidence survived. Capture does not
publish records or rewrite claim text. Capture and verify require an
idempotency key, which is reserved before external I/O so concurrent requests
cannot duplicate evidence or review events.

## Disclosure Expectations

- Do not publish exploit details before the maintainer has had a reasonable chance to reproduce and mitigate the issue.
- If you are unsure whether something is security-sensitive, report it privately first.
- Public bug reports without exploit details are still fine for clearly non-security defects.

## Maintainer Action Before Tagging

Before tagging or advertising a broader public preview:

- confirm GitHub private vulnerability reporting is enabled for the repository
- or replace the preferred reporting path above with another maintainer-owned private channel you actually monitor
