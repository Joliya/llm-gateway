from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.auth import key_may_use_alias
from app.core.circuit_breaker import circuit_breaker
from app.core.cost import compute_cost, cost_headers, usage_from_openai
from app.core.executor import _iter_attempts, _prepare_params, build_candidate_aliases
from app.core.logging_service import log_request
from app.core.router import RouteNotFound
from app.db.models import VirtualKey
from app.providers.openai_compat import DEFAULT_BASE_URL

_settings = get_settings()

# These endpoints are OpenAI-protocol passthroughs; only openai-compatible
# upstreams speak them. anthropic/gemini have entirely different APIs here.
OPENAI_TYPES = ("openai", "openai_compat")


def openai_headers(dep, *, json_body: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {dep.api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    if dep.org:
        headers["OpenAI-Organization"] = dep.org
    headers.update(dep.extra_headers or {})
    return headers


def upstream_base(dep) -> str:
    return (dep.base_url or DEFAULT_BASE_URL).rstrip("/")


async def resolve_aliases(session: AsyncSession, model: str | None, vk: VirtualKey):
    """Shared front-matter: validate model, key permission, and routing."""
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model'")
    if not key_may_use_alias(vk, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Key not allowed to use model {model!r}")
    try:
        return await build_candidate_aliases(session, model)
    except RouteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


async def json_passthrough(
    *,
    request: Request,
    session: AsyncSession,
    vk: VirtualKey,
    body: dict[str, Any],
    model: str | None,
    subpath: str,
    label: str,
):
    """Non-stream JSON proxy with the same routing / LB / fallback / logging as
    chat, for OpenAI-protocol endpoints (responses, images). Returns a FastAPI
    response (success carries the litellm cost header)."""
    from fastapi.responses import JSONResponse

    aliases = await resolve_aliases(session, model, vk)
    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        if dep.provider_type not in OPENAI_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"{label} not supported for provider {dep.provider_type}")
        params = _prepare_params(dep, body)
        params["model"] = dep.upstream_model
        url = f"{upstream_base(dep)}/{subpath}"
        try:
            resp = await client.post(url, headers=openai_headers(dep), json=params,
                                     timeout=_settings.request_timeout)
        except httpx.HTTPError as exc:
            circuit_breaker.record_failure(dep.deployment_id)
            last_status, last_body = 502, str(exc)
            continue
        if resp.status_code >= 400:
            if resp.status_code == 429 or resp.status_code >= 500:
                circuit_breaker.record_failure(dep.deployment_id)
                last_status, last_body = resp.status_code, resp.text
                continue
            return JSONResponse({"error": {"message": resp.text}}, status_code=resp.status_code)

        circuit_breaker.record_success(dep.deployment_id)
        data = resp.json()
        usage = usage_from_openai(data)
        cost = compute_cost(usage, dep.input_price, dep.output_price)
        await log_request(session, virtual_key_id=vk.id, requested_model=model, deployment=dep,
                          usage=usage, status=200, cost=cost,
                          latency_ms=int((time.monotonic() - started) * 1000), retries=0,
                          upstream_url=url, upstream_request=params, upstream_response=data)
        await session.commit()
        return JSONResponse(data, headers=cost_headers(cost))

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)
