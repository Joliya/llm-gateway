#!/bin/sh
set -e

# Bring the schema up to date before serving. Idempotent: a no-op when already
# at head. Honors GW_DATABASE_URL (sqlite or postgres) via alembic/env.py.
#
# Convenient for single-node / docker-compose (default on). In multi-replica
# deployments (K8s) DO NOT migrate from every pod — they would race on the same
# DDL. Set GW_RUN_MIGRATIONS=false on the app Deployment and run migrations once
# from a Job / initContainer (see deploy/k8s/migrate-job.yaml).
if [ "${GW_RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] applying migrations (GW_RUN_MIGRATIONS=true)..."
  alembic upgrade head
else
  echo "[entrypoint] skipping migrations (GW_RUN_MIGRATIONS=false)"
fi

exec "$@"
