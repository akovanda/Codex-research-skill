# Release Checklist

Use this for GitHub source releases in the current preview phase.

## Before tagging

1. Ensure the repo is clean.
2. Run `make preview-check`.
3. Run `make workflow-check`.
4. Run `make grounded-pass-check` when validating the built-in example research suite.
5. Review docs for support-matrix consistency.
6. Update `CHANGELOG.md` for the release.
7. Confirm the private security reporting path in `SECURITY.md` is real, enabled for the published repository, and not a placeholder for a real maintainer-owned security contact.
8. Confirm `[project.urls]` in `pyproject.toml` points at the published repository.
9. Confirm the `release image` workflow can publish `ghcr.io/akovanda/codex-research-skill:<version>`.
10. Confirm package-installed CLI smoke tests can run `research-registry up --skip-start --skip-pull` and install both managed skills without a source checkout.

Do not tag a public preview release until items 5 through 10 are complete.

## Verification commands

```bash
make preview-check
make workflow-check
make grounded-pass-check
make doctor
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
- package-manager CLI status and the required GHCR image tag
- any migration or operator notes
