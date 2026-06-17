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


@lru_cache
def get_settings() -> Settings:
    return Settings()
