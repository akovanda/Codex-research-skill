# Deposit retention and internal-error diagnostics

A request above the configured snapshot-retention ceiling is a policy rejection,
not an unavailable database. The write runtime now returns a named
`SNAPSHOT_POLICY_DENIED` error while retaining the existing authentication,
namespace, ingest-scope, rollback, and retention checks.

For an `evidence_only` server, a caller may supply transient source text for
hash and evidence-anchor verification; the source text is not persisted as a
snapshot. Preserve only the required evidence and a compact synthesis. Do not
copy a complete source into the report summary or increase the machine's
retention ceiling to work around a rejected request.

Unclassified failures keep the `DEPOSIT_FAILED` prefix and add a random
16-hex-character correlation ID. The corresponding server log records only
that ID and the exception class, not the exception message, SQL text, source
content, credentials, or traceback locals. Existing classified deposit errors
remain unchanged.

Focused verification: **55 passed, 2 skipped**, with one existing HTTP-client
deprecation warning. The exercised suites cover atomic deposit, validate-only
rollback, retention refusal, transient evidence-only capture, stable request
hashes, idempotent replay, conflicting keys, namespace isolation, MCP write
contracts, database-error redaction, and transport security. The optional tests
skipped by the environment are not claimed as exercised. This patch is source
and tests only; no running service was restarted or its policy changed.

Reproduce:

```sh
PYTHONPATH=src:. python -m pytest -q \
  tests/test_v2_deposit_diagnostics.py tests/test_v2_deposit.py \
  tests/test_mcp_v2_write.py tests/test_mcp_deposit_database_errors.py \
  tests/test_mcp_transport_security.py
```
