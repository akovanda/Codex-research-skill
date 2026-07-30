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

Offline `SHA256SUMS`, SPDX 2.3 SBOM, and in-toto/SLSA-shaped provenance drafts
are generated under `.data/release/<version>/`. Local provenance explicitly records a
dirty working tree and is unsigned. The manual release-artifacts workflow
uploads CI artifacts for maintainer review but does not publish a package or
container. The separate image workflow is manual dispatch only.
