from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import ResolvedDeployment
from app.db.models import RequestLog
from app.providers.base import Usage


async def log_request(
    session: AsyncSession,
    *,
    virtual_key_id: Optional[int],
    requested_model: str,
    deployment: Optional[ResolvedDeployment],
    usage: Optional[Usage],
    status: int,
    cost: float,
    latency_ms: int,
    retries: int,
    cache_hit: bool = False,
    error: Optional[str] = None,
) -> None:
    usage = usage or Usage()
    log = RequestLog(
        virtual_key_id=virtual_key_id,
        requested_model=requested_model,
        alias=deployment.alias_name if deployment else None,
        deployment_id=deployment.deployment_id if deployment and deployment.deployment_id > 0 else None,
        provider_type=deployment.provider_type if deployment else None,
        status=status,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cost=cost,
        latency_ms=latency_ms,
        retries=retries,
        cache_hit=cache_hit,
        error=error,
    )
    session.add(log)
    await session.flush()
