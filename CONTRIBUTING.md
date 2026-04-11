# Contributing

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

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
