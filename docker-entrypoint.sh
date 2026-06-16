#!/bin/sh
set -e

# Bring the schema up to date before serving. Idempotent: a no-op when already
# at head. Honors GW_DATABASE_URL (sqlite or postgres) via alembic/env.py.
echo "[entrypoint] applying migrations..."
alembic upgrade head

exec "$@"
