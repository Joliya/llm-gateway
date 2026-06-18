from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GW_", extra="ignore")

    # --- Core ---
    app_name: str = "llm-gateway"
    debug: bool = False

    # Master key protecting the /admin API. Required in production.
    master_key: str = "change-me-master-key"

    # How long a console user session token stays valid after login (hours).
    session_ttl_hours: float = 12.0

    # Fernet key used to encrypt credential api_keys at rest.
    # If empty, a key is derived from master_key (fine for single-node dev).
    encryption_key: str = ""

    # --- Storage ---
    # Default: local SQLite. Switch to postgres via
    #   GW_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
    database_url: str = "sqlite+aiosqlite:///./llm_gateway.db"

    # Optional Redis for rate-limiting + cache across instances.
    # When empty, in-memory backends are used (single-node only).
    redis_url: str = ""

    # Create tables via SQLAlchemy on startup (zero-config dev). Set false when
    # managing the schema with Alembic migrations, then run `alembic upgrade head`.
    auto_create_tables: bool = True

    # Connection pool (ignored for SQLite). Tune for the expected per-pod
    # concurrency; total upstream DB connections ≈ replicas × (size + overflow),
    # so keep that under Postgres max_connections (or front it with PgBouncer).
    db_pool_size: int = 10                  # persistent connections per process
    db_max_overflow: int = 20               # extra burst connections beyond pool_size
    db_pool_timeout: float = 30.0           # seconds to wait for a free connection
    db_pool_recycle: int = 1800             # recycle connections older than this (s)

    # --- Proxy behaviour ---
    # Whether the upstream HTTP client honors HTTP(S)_PROXY / ALL_PROXY env vars.
    # Set false to always connect to providers directly (ignores system proxies).
    trust_env: bool = True

    request_timeout: float = 120.0          # upstream call timeout (seconds)
    max_retries: int = 2                    # retries within an alias pool
    retry_backoff_base: float = 0.5         # exponential backoff base (seconds)

    # Circuit breaker
    cb_failure_threshold: int = 5           # consecutive failures before opening
    cb_cooldown_seconds: float = 30.0       # how long a deployment stays cooled down

    # Cache
    cache_enabled: bool = False             # global default; can be per-alias later
    cache_ttl_seconds: int = 300

    # Config snapshot TTL: how long the router caches DB config in memory.
    config_cache_ttl_seconds: float = 5.0

    # --- Observability ---
    # Capture the exact JSON body sent to the provider and the raw upstream
    # response in each request log. Lets you verify param translation (e.g. that
    # the right thinking/reasoning fields were sent). Bodies include prompt
    # content, so disable if that is a privacy concern.
    log_upstream_io: bool = True
    # Cap stored upstream response strings to this many chars (0 = unlimited).
    log_upstream_max_chars: int = 20000

    # Write request logs off the request path via an in-process queue + a
    # background worker that batches inserts. Cuts tail latency and per-request
    # commits under load. Ignored for SQLite (always logs inline, to avoid the
    # single-writer lock contention). Set false to force synchronous logging.
    log_async: bool = True
    log_queue_max: int = 10000              # bounded queue; overflow is dropped + counted
    log_batch_size: int = 100               # max rows per worker transaction
    log_flush_interval: float = 0.5         # max seconds the worker waits before flushing

    # Expose Prometheus metrics at GET /metrics.
    metrics_enabled: bool = True
    # Record mutating /admin calls (POST/PATCH/PUT/DELETE) to admin_audit_logs.
    admin_audit_enabled: bool = True

    # Background sweep that resets idle keys' daily/monthly budget windows.
    # 0 disables the sweeper (on-access reset_if_needed still runs).
    budget_sweep_interval: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
