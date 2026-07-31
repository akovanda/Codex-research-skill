# V2 Release Status

The draft package and plugin metadata is aligned at `0.2.0a1` and is classified
as alpha. The released preview remains `0.1.0`. RR2-014 adds release evidence
and commands; it does not declare a
stable release, create a tag, sign artifacts, publish a package, or push an
image.

The checked-in synthetic corpus currently meets the numeric stable retrieval
thresholds: Recall@5 `1.00`, evidence availability `1.00`, and exact lookup
Recall@1 `1.00`. State accuracy and nDCG@10 are `1.00`; Precision@5 is `0.20`
under the fixed five-result denominator. One of two no-answer cases returns
lexically related non-rejected records, so no-answer accuracy is `0.50`.
Latency values are host-specific.

These synthetic results do not establish stable readiness. The following
operator evidence is not checked in and must not be guessed:

- SQLite/Postgres top-five overlap on the deterministic corpus;
- successful migration and use of at least one real v1 database;
- shared Postgres/Compose upgrade and rollback rehearsal;
- operator-local private known-answer corpus results;
- completed security review and explicit acceptance/resolution of findings;
- maintainer signing/attestation of clean-commit release artifacts.

Without that evidence, the composed automated gate can demonstrate alpha only.
`make rr2-beta-check` and `make rr2-stable-check` correctly fail. Kubernetes
remains example-only, public-internet deployment is not a supported default,
PDF/complex parsers remain unsupported without a future isolated optional
extra, embeddings remain optional and absent, and there is no agent loop or
automatic publication.

Deposit now resolves exact evidence anchors whenever supplied or retained
UTF-8 content provides the selector's required representation. Exact
mismatches and ambiguous matches reject the entire deposit atomically;
unavailable page/DOM indexes or absent content remain explicitly unverified
with bounded warnings and per-evidence metadata. PostgreSQL deposit parity now covers concurrent identical requests,
same-key conflicting requests, and distinct-key requests that converge on one
canonical claim. Transaction-scoped advisory identity locks serialize topic,
question, source, and canonical-claim find-or-create decisions. The remaining
post-alpha correctness follow-up is to narrow psycopg exception translation so
programming and schema failures retain distinct stable diagnostics. Retained v1 source creation, snapshot locators, and URL
import now reuse the same secret-bearing HTTP(S) validator as v2 before dedupe,
persistence, or external I/O. Before exposing a shared/public alpha,
administrators must either supply an explicit namespace for generic creates or
the documented
contract must intentionally retain the `user/local` default, and native v2
publication must eventually traverse authoritative `claim_evidence`
relationships. The alpha currently fails closed instead: native-v2 deposit
graphs cannot be published, and public stable sources, claims, and questions
cannot receive private versions, evidence, revisions, runs, reports, or
revision-producing review actions. Capture is disabled for public sources.
Retained v1 publication remains available only for its complete static
compatibility graph.
Shared beta must also provide an unambiguous namespace-aware global-admin
deposit-receipt lookup and replace random-ID tie-breaking with an explicit
deterministic sequence for runtime review events that share a timestamp.

Offline `SHA256SUMS`, SPDX 2.3 SBOM, and in-toto/SLSA-shaped provenance drafts
are generated under `.data/release/<version>/`. Local provenance explicitly records a
dirty working tree and is unsigned. The manual release-artifacts workflow
uploads CI artifacts for maintainer review but does not publish a package or
container. The separate image workflow is manual dispatch only.
