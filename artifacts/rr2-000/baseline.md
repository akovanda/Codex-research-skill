# RR2-000 v1 baseline

- Baseline commit: `4f2163083570a22b613e950dd3a78f010854a3a4`
- Capture date: 2026-07-30
- Python: 3.12.3
- Package: `research-registry 0.1.0`
- FastAPI: 0.141.1
- Pydantic: 2.13.4
- MCP SDK: 1.29.0
- psycopg: 3.3.4

## Baseline checks

- The 18 existing test modules completed with 79 passed, 3 skipped, and one
  third-party deprecation warning in 20.72 seconds.
- `.venv/bin/python -m build` built the wheel and sdist successfully.
- `.venv/bin/python -m research_registry --help` rendered CLI help without
  starting a server.

## Current command surface

- `make test`
- `make build`
- `make preview-check`
- `make workflow-check`
- `make grounded-pass-check`

## Current schema

| Migration | SHA-256 |
|---|---|
| `0001_initial` | `ea4c8ad9e9773fee7f19adc31a247ddc2aa4cbd14fd544418c80b802c6cf278e` |
| `0002_workflows_and_trust` | `f360daa9ab2fc3a35b4157ab0766961e7ec7cf1699f4c144a21692f7b5dda54c` |

The checked-in HTTP, MCP, and SQLite inventory snapshots are
`tests/contracts/v1_openapi.json`, `tests/contracts/v1_mcp_tools.json`, and
`tests/contracts/v1_database_inventory.json`.

The real private-registry audit is intentionally pending operator execution.
Only synthetic and aggregate artifacts belong in the repository.
