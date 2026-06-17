from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.config_store import ResolvedDeployment
from app.db.models import RequestLog
from app.providers.base import Usage

_settings = get_settings()


def _capture(value: Any) -> Any:
    """Honor the GW_LOG_UPSTREAM_IO toggle and cap oversized string payloads."""
    if not _settings.log_upstream_io or value is None:
        return None
    cap = _settings.log_upstream_max_chars
    if cap and isinstance(value, str) and len(value) > cap:
        return value[:cap] + f"\n…[truncated {len(value) - cap} chars]"
    return value


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
    upstream_url: Optional[str] = None,
    upstream_request: Optional[dict[str, Any]] = None,
    upstream_response: Any = None,
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
        upstream_url=upstream_url,
        upstream_request=_capture(upstream_request),
        upstream_response=_capture(upstream_response),
    )
    session.add(log)
    await session.flush()
