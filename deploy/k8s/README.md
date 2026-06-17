# Deploying llm-gateway on Kubernetes

Manifests for a horizontally-scalable deployment. They externalize all shared
state to Postgres + Redis and keep schema migrations out of the app pods.

## Prerequisites

- A Postgres database and a Redis instance reachable from the cluster.
- The image pushed to a registry your cluster can pull. Replace
  `llm-gateway:latest` in `migrate-job.yaml` and `deployment.yaml`.

## Order of operations

1. **Create the secret** (don't use `secret.example.yaml` values in production):
   ```sh
   kubectl create secret generic llm-gateway-secrets \
     --from-literal=GW_MASTER_KEY=... \
     --from-literal=GW_ENCRYPTION_KEY=... \
     --from-literal=GW_DATABASE_URL='postgresql+asyncpg://user:pass@postgres:5432/llm_gateway' \
     --from-literal=GW_REDIS_URL='redis://redis:6379/0'
   ```
   Set `GW_ENCRYPTION_KEY` explicitly and keep it stable — rotating the master
   key must not orphan credentials encrypted under the old derived key.

2. **Migrate once** (before rolling out app pods):
   ```sh
   kubectl delete job llm-gateway-migrate --ignore-not-found
   kubectl apply -f deploy/k8s/migrate-job.yaml
   kubectl wait --for=condition=complete job/llm-gateway-migrate --timeout=120s
   ```

3. **Deploy the app**:
   ```sh
   kubectl apply -f deploy/k8s/deployment.yaml
   ```

The app pods run with `GW_RUN_MIGRATIONS=false`, so scaling to N replicas never
triggers N concurrent `alembic upgrade head`. Run the migrate Job on every
version bump that ships a new migration. (An initContainer that runs the Job's
command works too, but a Job gives one run per rollout instead of one per pod.)

## Scaling notes

- **Set `GW_REDIS_URL`** so rate limits and the response cache are shared across
  pods. Without it each pod limits/caches independently.
- **DB connections**: total ≈ `replicas × (GW_DB_POOL_SIZE + GW_DB_MAX_OVERFLOW)`.
  Keep that under Postgres `max_connections`, or put PgBouncer in front.
- **Per-pod, best-effort state** (not globally coordinated): the circuit breaker,
  the round-robin cursor, and `least_busy` in-flight counts live in each pod's
  memory. Failover still works; just don't expect a single global view. Use
  `weighted` or `random` balancing if you want fully stateless selection.
