# Shared Deployment With Compose

This is the recommended shared-team deployment for the current developer preview.

It keeps the product shape simple:

- one app container
- one Postgres container
- API keys plus admin token
- one shared internal backend for a team or org

## Files

- [`Dockerfile`](../Dockerfile)
- [`deploy/compose.yaml`](../deploy/compose.yaml)
- [`deploy/.env.example`](../deploy/.env.example)

## Start

```bash
cp deploy/.env.example deploy/.env
docker compose -f deploy/compose.yaml --env-file deploy/.env up --build
```

The app runs migrations on startup before serving traffic.

## Required environment

- `RESEARCH_REGISTRY_DATABASE_URL`
- `RESEARCH_REGISTRY_ADMIN_TOKEN`
- `RESEARCH_REGISTRY_SESSION_SECRET`
- `RESEARCH_REGISTRY_PUBLIC_BASE_URL`

## Bootstrap

Create an org:

```bash
curl -X POST \
  -H "x-admin-token: ${RESEARCH_REGISTRY_ADMIN_TOKEN}" \
  -H "content-type: application/json" \
  http://127.0.0.1:8000/api/admin/organizations \
  -d '{"org_id":"acme","display_name":"Acme"}'
```

Issue an org-scoped API key:

```bash
curl -X POST \
  -H "x-admin-token: ${RESEARCH_REGISTRY_ADMIN_TOKEN}" \
  -H "content-type: application/json" \
  http://127.0.0.1:8000/api/admin/api-keys \
  -d '{"label":"acme-writer","actor_user_id":"owner","actor_org_id":"acme","namespace_kind":"org","namespace_id":"acme","scopes":["ingest","publish","read_private"]}'
```

## Notes

- Compose is for internal or VPN-restricted deployment, not public multi-tenant hosting
- shared mode should use Postgres, not SQLite
