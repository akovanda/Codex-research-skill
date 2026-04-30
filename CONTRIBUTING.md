# Contributing

## Choose A Path

- If you are evaluating the preview as a user, start with `make up` and [docs/getting-started.md](docs/getting-started.md).
- If you are changing code, use the development path below and run the relevant validation gates before opening a pull request.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Common commands:

```bash
make test
make workflow-check
make preview-check
```

What they do:

- `make test` runs the default test suite
- `make workflow-check` runs the HTTP end-to-end test plus the built-in research harnesses
- `make preview-check` runs the broader preview gate, including packaging and smoke checks

If you change public docs, packaging metadata, install behavior, or deployment assets, run `make preview-check` before you ask for review.

## Expectations

- keep the canonical model question-led: question, session, excerpt, claim, report
- preserve localhost-first usability
- keep shared deployment assumptions self-hosted and explicit
- add or update tests with behavior changes
- do not silently widen the canonical public API without updating the README and docs

## Pull requests

- keep changes scoped and reviewable
- include verification notes
- update docs when public behavior changes
- call out whether the change affects localhost preview, shared Compose, repo-aware capture, or packaging
