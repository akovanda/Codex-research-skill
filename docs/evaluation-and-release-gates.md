# Evaluation and Release Gates

Research Registry evaluates durable evidence reuse, not agent reasoning. The
checked-in harnesses are deterministic and offline. They do not browse, invoke
a model, synthesize claims, deposit records, or publish anything.

## Checked-in synthetic retrieval

Run:

```bash
research-registry eval-retrieval \
  --corpus evals/retrieval/synthetic.json \
  --release-level stable
```

The result includes Recall@1/5/10, MRR, nDCG@10, Precision@5, exact-lookup
Recall@1, evidence availability, review/conflict/freshness accuracy, duplicate
rate, no-answer accuracy, SQLite/Postgres top-five overlap when configured,
p50/p95 search latency, response bytes, search calls, and the rate of useful
answers found within two calls.

The checked-in corpus labels are fixed. Do not change expected records, evidence
minimums, state labels, or release thresholds in response to a poor result.
Add a new reviewed case when product behavior intentionally changes.

Latency is measured locally and therefore varies by host. Ranking, labels, and
the quality metrics are deterministic. Precision@5 uses five as the denominator
even when a query has fewer than five relevant answers; a corpus with one
relevant answer per query can therefore have perfect Recall and nDCG with
Precision@5 of `0.20`.

## Operator-local known answers

Private corpora stay outside the repository. A local file uses:

```json
{
  "protocol": "research-known-answer-corpus/v1",
  "cases": [
    {
      "id": "case-001",
      "query": "paraphrased future question",
      "scope": {"repository": "artifact"},
      "expected_record_ids": ["clm_example"],
      "relevant_evidence_ids": ["evd_example"],
      "expected_state": {
        "review": "reviewed",
        "conflict": "none",
        "freshness": "fresh"
      },
      "notes": "operator-only rationale"
    }
  ]
}
```

Run it against a copied or explicitly selected existing registry:

```bash
research-registry eval-known-answers \
  --database /safe/copy/registry.sqlite3 \
  --corpus /private/path/known-answers.json
```

The evaluator does not initialize or mutate the selected database. Output omits
queries, notes, source bodies, claims, quotes, and prompts. It includes record
and evidence IDs because they are needed to diagnose labels, so the result
should still be treated as private operator data.

## Recorded comparative experiment

The four required modes are:

- `memory_only`
- `registry_only`
- `both`
- `research_again`

The harness scores caller-recorded observations. It is deliberately not an
agent loop:

```bash
research-registry eval-comparative \
  --corpus evals/comparative/synthetic.json
```

Each mode records correct-prior-finding, expected and resolved citation counts,
tool calls, context bytes, latency, user corrections, and whether repeated
research was avoided. Real comparative runs remain local when their questions
or outcomes are private.

## Content-free local health

```bash
research-registry metrics --local --since 30d \
  --database /safe/copy/registry.sqlite3
```

This reports evidence relationship and anchor health, source hash health,
claim-revision integrity, durable deposit reservations, migration warnings and
errors, and aggregate row counts. It never returns content fields. Historical
operation latency and error rates are reported as unavailable because v2 does
not yet have a content-free operation-event store and outbound telemetry is
disabled.

## Fixed release commands

```bash
make rr2-release-check
make rr2-alpha-check
make rr2-beta-check OPERATOR_EVIDENCE=/private/path/operator-evidence.json
make rr2-stable-check OPERATOR_EVIDENCE=/private/path/operator-evidence.json
```

`rr2-release-check` prints every constituent command and then assesses the
highest demonstrated level. Alpha, beta, and stable targets require their
respective fixed gates and exit nonzero when evidence is missing.

Operator evidence is a private JSON object containing exactly:

```json
{
  "real_v1_migration": true,
  "shared_compose": true,
  "security_review": true
}
```

These booleans are attestations by the operator, not values the automated suite
can infer. Beta requires a real v1 migration plus shared Compose/Postgres
rehearsal. Stable additionally requires completed security review and all fixed
stable metrics.

## Security and privacy evaluation

`make rr2-security-check` composes the SSRF address/redirect/proxy/limit suite,
parser-bomb cases, deterministic malformed-contract cases, private-data checks,
Git containment and credential neutrality, full deposit fault injection, and
sensitive-log scanning. To scan an operator log without printing matches:

```bash
python scripts/rr2_scan_logs.py /private/path/registry.log \
  --forbid-file /private/path/private-sentinels.txt
```

The scanner reports only finding kind, line number, and a short one-way
fingerprint. Log and sentinel files are bounded to 5 MiB.
