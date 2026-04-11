# Kubernetes Deployment

Kubernetes is the portable self-host path for teams that already manage cluster infrastructure.

The manifests in [`deploy/kubernetes`](../deploy/kubernetes) assume:

- the app runs in-cluster
- Postgres is available as an external service or separately managed dependency
- the admin token and session secret come from Kubernetes secrets

## Manifests

- `configmap.yaml`
- `secret.example.yaml`
- `deployment.yaml`
- `service.yaml`
- `ingress.example.yaml`
- `migrate-job.yaml`

## Flow

1. Create secrets from real values.
2. Set `RESEARCH_REGISTRY_DATABASE_URL` to the shared Postgres instance.
3. Run the migration job.
4. Deploy the app.
5. Expose the service internally or through an ingress.
6. Bootstrap orgs and API keys with the admin endpoints.

## Notes

- these manifests are examples, not a production-hardening guide
- the current preview uses API keys plus admin token, not OIDC
- Kubernetes deployment is for self-hosted shared backends, not the future public shared service
