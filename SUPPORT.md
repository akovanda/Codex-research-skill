# Support

Research Registry is in developer preview. Support is intentionally narrow.

## Where To Start

- If you are brand new, start with [README.md](README.md) and [docs/getting-started.md](docs/getting-started.md).
- If you want the managed localhost install, read [docs/deploy-local.md](docs/deploy-local.md).
- If you want a shared self-hosted setup, read [docs/deploy-shared-compose.md](docs/deploy-shared-compose.md).
- If you want the API flow, read [docs/api-quickstart.md](docs/api-quickstart.md).
- If you want Codex-specific behavior, read [docs/implicit-research-capture.md](docs/implicit-research-capture.md) and [docs/repo-aware-capture.md](docs/repo-aware-capture.md).

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
- Use GitHub issues for documentation corrections and preview-scope clarifications.
- There is no support SLA in this preview.
- Questions that are really product design requests or broad consulting requests may be closed or redirected.

Good issue subjects:

- `local install fails on macOS with Docker Desktop running`
- `repo-aware capture picked the wrong test command for package.json workspace`
- `README says X but make status reports Y`

Bad issue subjects:

- `make it work with every monolith`
- `please design my whole memory architecture`
- `does this fit my undisclosed internal setup`

When filing an issue, include:

- exact commit or release tag
- platform and Docker version
- the command you ran
- the relevant log or failing output
- whether you were using `make up`, shared Compose, or a repo-local development path

## Scope Boundaries

- Localhost preview support assumes Docker with Compose support and Python 3.12.
- Shared Compose support assumes you operate the network boundary, DNS, and TLS layer yourself.
- Security reports should follow the private disclosure path documented in [SECURITY.md](SECURITY.md).
