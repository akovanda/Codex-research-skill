# FAQ

## Do I need Docker?

For the supported localhost preview path, yes.

`make up` starts a managed local service plus Postgres in Docker Compose and wires Codex to that shared localhost runtime.

If you only want a repo-local development process, you can still run `research-registry-web` against SQLite, but that is a development path, not the main preview install.

## Do I need Codex to use this?

No.

The main product surface is the web app and JSON API. Codex, MCP, and the checked-in skills sit on top of that and are the primary workflow this preview is optimized for.

## What does `make up` change on my machine?

It does five visible things:

- creates or reuses `.venv/`
- installs the package in editable mode
- creates managed config under `~/.config/research-registry/`
- starts the managed localhost runtime on `127.0.0.1:8010`
- patches `~/.codex/config.toml` and installs the managed skill symlinks if Codex is present

If you want to remove the managed integration, run `make uninstall`. If you also want to delete the managed local data and Docker volumes, run `make purge-local`.

## Does anything get published automatically?

No.

Implicit capture stores new records privately by default. Publishing is a separate explicit action.

## Can I use SQLite instead of Postgres?

Yes for repo-local development.

The supported localhost preview path uses Postgres in Docker Compose. SQLite remains available for local development or compatibility workflows when you run the app directly.

## What operating systems are supported?

Preview support today:

- Linux: primary target and CI-covered
- macOS: intended localhost preview target
- Windows: not currently claimed

## Is this meant for public internet exposure?

Not in this preview.

The supported network shapes are:

- one developer running a shared localhost service for local Codex sessions
- one team running a self-hosted shared server on a private network

If you expose it publicly, you own the network controls, auth posture, TLS, and operational hardening.

## Do I need a repo profile for repo-aware capture?

No.

The best behavior comes from a checked-in `.codex/repo-profile.toml`, but repo-aware capture can fall back to nearest `AGENTS.md` files plus local manifests like `Cargo.toml`, `package.json`, `Gemfile`, and `pyproject.toml`.

## Where should I file bugs versus product requests?

- File reproducible defects in GitHub issues.
- File documentation mistakes in GitHub issues.
- Security-sensitive reports should follow [SECURITY.md](../SECURITY.md).
- Broad product direction requests may be narrowed, deferred, or redirected if they are not actionable preview work.
