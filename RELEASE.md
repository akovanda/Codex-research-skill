# Release Checklist

Use this for GitHub source releases in the current preview phase.

## Before tagging

1. Ensure the repo is clean.
2. Run the full test suite.
3. Verify the managed localhost installer path.
4. Verify the shared Compose path.
5. Set `[project.urls]` in `pyproject.toml` to the real published repository location.
6. Review docs for support-matrix consistency.
7. Update `CHANGELOG.md` for the release.

## Verification commands

```bash
. .venv/bin/activate
pytest -q
python -m build
research-registry-local-install
research-registry-local-status
research-registry-local-stop
```

## Tagging

Example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Release notes should include

- release type: open-source preview
- supported runtime modes
- unsupported or example-only deployment paths
- any migration or operator notes
