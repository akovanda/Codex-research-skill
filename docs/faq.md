# FAQ

## Do I need Docker?

No. The personal default uses SQLite, private XDG data, filesystem blobs, and
tokenless STDIO MCP. Run `research-registry init` or `make init`.

Docker with Compose is retained only for shared/team Postgres deployments and
existing managed installations. Use `research-registry up` or `make shared-up`
for that separate path.

## Do I need Codex to use this?

No.

No. The optional web app and JSON API can run independently. The personal
default is optimized for Codex and MCP, but the SQLite database and review
server are normal local application surfaces.

## What does `research-registry init` change on my machine?

It does three visible things:

- creates managed config under `~/.config/research-registry/`
- creates SQLite and content-addressed blob storage under
  `~/.local/share/research-registry/`
- applies additive migrations

It starts no service, modifies no Codex config, downloads no container, and
stores no API/auth token. `install-codex` separately installs the focused
plugin under `CODEX_HOME`.

Earlier `up`, `repair`, `status`, `token`, `down`, and `uninstall` commands
remain for the retained managed Docker/Postgres path.

## Do I need Homebrew, pipx, or uv?

No single package manager is required.

Use any Python package workflow that installs Python 3.12+ wheel/sdist
artifacts. `pipx install research-registry` is the clean persistent path after
a PyPI release. Homebrew is not required.

## Does anything get published automatically?

No.

Implicit capture stores new records privately by default. Publishing is a separate explicit action.

## Can I use SQLite instead of Postgres?

Yes. SQLite is the supported personal default. Postgres is the supported shared
backend.

The two dialects share logical application contracts and additive migrations,
but their database files/dumps are not interchangeable. Follow
[Managed Postgres migration choices](migrate-managed-postgres.md) for an
existing deployment.

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
