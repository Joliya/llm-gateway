from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import (
    aliases as admin_aliases,
)
from app.api.admin import (
    credentials as admin_credentials,
)
from app.api.admin import (
    deployments as admin_deployments,
)
from app.api.admin import (
    health as admin_health,
)
from app.api.admin import (
    keys as admin_keys,
)
from app.api.admin import (
    logs as admin_logs,
)
from app.api.admin import (
    playground as admin_playground,
)
from app.api.admin import (
    providers as admin_providers,
)
from app.api.v1 import chat, completions, embeddings, models
from app.config import get_settings
from app.core import budget as budget_mod
from app.core import cache as cache_mod
from app.core import metrics as metrics_mod
from app.core import rate_limiter as rl_mod
from app.core.logging_service import request_logger
from app.core.middleware import RequestContextMiddleware
from app.db.base import Base
from app.db.session import SessionLocal, engine

settings = get_settings()
_log = logging.getLogger("llm_gateway.main")


async def _budget_sweep_loop() -> None:
    """Periodically reset idle keys' rolled-over daily/monthly budget windows."""
    interval = settings.budget_sweep_interval
    while True:
        await asyncio.sleep(interval)
        try:
            async with SessionLocal() as session:
                await budget_mod.sweep_expired(session)
        except Exception:
            _log.exception("budget sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables for zero-config dev. Disable (GW_AUTO_CREATE_TABLES=false)
    # and use Alembic migrations when you need controlled schema upgrades.
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    app.state.http_client = httpx.AsyncClient(trust_env=settings.trust_env)

    # Swap in Redis backends when configured (multi-instance deployments).
    if settings.redis_url:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        rl_mod.rate_limiter.backend = rl_mod.RedisRateLimiter(client)
        cache_mod.response_cache.backend = cache_mod.RedisCache(client)
        app.state.redis = client

    # Background batch writer for request logs (no-op on SQLite / when disabled).
    await request_logger.start()

    # Periodic budget-window reset for idle keys.
    sweep_task: asyncio.Task | None = None
    if settings.budget_sweep_interval and settings.budget_sweep_interval > 0:
        sweep_task = asyncio.create_task(_budget_sweep_loop())

    try:
        yield
    finally:
        if sweep_task is not None:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
        await request_logger.stop()
        await app.state.http_client.aclose()
        if getattr(app.state, "redis", None) is not None:
            await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    # Stamp every request with a correlation id and audit admin mutations.
    app.add_middleware(RequestContextMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    if settings.metrics_enabled:
        @app.get("/metrics", include_in_schema=False)
        async def metrics():
            from prometheus_client import CONTENT_TYPE_LATEST

            return Response(metrics_mod.render(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/")

    # OpenAI-compatible proxy surface
    app.include_router(chat.router, prefix="/v1", tags=["proxy"])
    app.include_router(completions.router, prefix="/v1", tags=["proxy"])
    app.include_router(embeddings.router, prefix="/v1", tags=["proxy"])
    app.include_router(models.router, prefix="/v1", tags=["proxy"])

    # Management API
    app.include_router(admin_providers.router, prefix="/admin/providers", tags=["admin:providers"])
    app.include_router(admin_credentials.router, prefix="/admin/credentials", tags=["admin:credentials"])
    app.include_router(admin_aliases.router, prefix="/admin/aliases", tags=["admin:aliases"])
    app.include_router(admin_deployments.router, prefix="/admin/deployments", tags=["admin:deployments"])
    app.include_router(admin_keys.router, prefix="/admin/keys", tags=["admin:keys"])
    app.include_router(admin_logs.router, prefix="/admin", tags=["admin:logs"])
    app.include_router(admin_health.router, prefix="/admin", tags=["admin:health"])
    app.include_router(admin_playground.router, prefix="/admin", tags=["admin:playground"])

    # Self-contained admin console (static, no build step). Served last so it
    # never shadows the API routes above.
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    return app


app = create_app()
