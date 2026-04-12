# Support

Research Registry is in developer preview. Support is intentionally narrow.

## Supported Preview Paths

- managed localhost runtime for Codex-first developers on Linux
- managed localhost runtime for Codex-first developers on macOS
- shared self-hosted Compose deployment for internal teams on private networks

Not currently claimed:

- Windows localhost installs
- direct public-internet exposure
- Kubernetes as a production-hardened target
- hosted multi-tenant service support

## Community Intake

- Use GitHub issues for concrete, reproducible bugs.
- There is no support SLA in this preview.
- Questions that are really product design requests or broad consulting requests may be closed or redirected.

When filing an issue, include:

- exact commit or release tag
- platform and Docker version
- the command you ran
- the relevant log or failing output
- whether you were using `make up`, shared Compose, or a repo-local development path

## Scope Boundaries

- Localhost preview support assumes Docker with Compose support and Python 3.12.
- Shared Compose support assumes you operate the network boundary, DNS, and TLS layer yourself.
- Security reports should follow the private disclosure path documented in `SECURITY.md` once that file is updated with a real contact.
