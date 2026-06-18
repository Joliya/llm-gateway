from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.auth import authenticate_virtual_key, key_may_use_alias
from app.core.circuit_breaker import circuit_breaker
from app.core.executor import _iter_attempts, _prepare_params, build_candidate_aliases
from app.core.logging_service import log_request
from app.core.router import RouteNotFound
from app.db.models import VirtualKey
from app.db.session import get_session
from app.providers.base import Usage
from app.providers.openai_compat import DEFAULT_BASE_URL

router = APIRouter()
_settings = get_settings()


@router.post("/completions")
async def completions(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """Legacy text completions. Supported for OpenAI-compatible providers only."""
    body: dict[str, Any] = await request.json()
    model = body.get("model")
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model'")
    if not key_may_use_alias(vk, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Key not allowed to use model {model!r}")

    try:
        aliases = await build_candidate_aliases(session, model)
    except RouteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        if dep.provider_type not in ("openai", "openai_compat"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"/completions not supported for provider {dep.provider_type}")
        params = _prepare_params(dep, body)
        params["model"] = dep.upstream_model
        params.pop("stream", None)
        base = (dep.base_url or DEFAULT_BASE_URL).rstrip("/")
        headers = {"Authorization": f"Bearer {dep.api_key}", "Content-Type": "application/json"}
        headers.update(dep.extra_headers or {})
        try:
            resp = await client.post(f"{base}/completions", headers=headers, json=params,
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
        u = data.get("usage") or {}
        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("total_tokens", 0))
        await log_request(session, virtual_key_id=vk.id, requested_model=model, deployment=dep,
                          usage=usage, status=200, cost=0.0,
                          latency_ms=int((time.monotonic() - started) * 1000), retries=0)
        await session.commit()
        return JSONResponse(data)

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)
