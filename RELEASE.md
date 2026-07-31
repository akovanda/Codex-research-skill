# Release Checklist

Use this for GitHub source releases in the current alpha preview. Read
`docs/release-status.md`, `docs/evaluation-and-release-gates.md`, and
`docs/upgrade-and-rollback.md` first.

## Before tagging

1. Ensure the repo is clean.
2. Run `make rr2-release-check` and retain every constituent result.
3. Run the environment-specific Postgres/shared Compose checks.
4. Run the private known-answer corpus and record only content-safe aggregates.
5. Review docs for support-matrix consistency.
6. Update `CHANGELOG.md` for the release.
7. Confirm the private security reporting path in `SECURITY.md` is real, enabled for the published repository, and not a placeholder for a real maintainer-owned security contact.
8. Confirm `[project.urls]` in `pyproject.toml` points at the published repository.
9. Confirm wheel and sdist clean-HOME smoke can initialize SQLite, install the
   focused plugin, and complete STDIO status/search without Docker.
10. Generate and review checksums, SBOM, and provenance.
11. Complete the copied-data upgrade/rollback runbook.
12. Require the intended fixed release level:

    ```bash
    make rr2-alpha-check
    make rr2-beta-check OPERATOR_EVIDENCE=/private/path/operator-evidence.json
    make rr2-stable-check OPERATOR_EVIDENCE=/private/path/operator-evidence.json
    ```

Do not tag a public preview release until all applicable items are complete.
Beta/stable failures are blockers, not invitations to change labels or
thresholds.

## Verification commands

```bash
make rr2-release-check
python -m build
```

The prior preview regression commands remain available as non-release-level
constituents:

```bash
make preview-check
make workflow-check
make grounded-pass-check
```

Tagging, package publication, image publication, and signing are explicit
maintainer actions. No repository script or tag-triggered workflow performs
them automatically.

## Release notes should include

- release type: open-source preview
- supported runtime modes
- unsupported or example-only deployment paths
- package-manager CLI status and no-Docker personal path
- GHCR image tag for the separate shared Compose path
- any migration or operator notes
- achieved release gate and every unmet higher-level gate
- retrieval, evidence, migration, deposit, security, and rehearsal results
- checksums, SBOM, provenance signature/attestation status
