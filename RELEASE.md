# Release Checklist

Use this for GitHub source releases in the current preview phase.

## Before tagging

1. Ensure the repo is clean.
2. Run `make preview-check`.
3. Review docs for support-matrix consistency.
4. Update `CHANGELOG.md` for the release.
5. Set a real maintainer-owned security contact in `SECURITY.md`.
6. Set `[project.urls]` in `pyproject.toml` to the real published repository location.

Do not tag a public preview release until items 5 and 6 are complete.

## Verification commands

```bash
make preview-check
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
