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
from app.core.executor import build_candidate_aliases, _iter_attempts, _prepare_params
from app.core.logging_service import log_request
from app.core.router import RouteNotFound
from app.db.models import VirtualKey
from app.db.session import get_session
from app.providers.base import Usage
from app.providers.registry import get_adapter

router = APIRouter()
_settings = get_settings()


@router.post("/embeddings")
async def embeddings(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    body: dict[str, Any] = await request.json()
    model = body.get("model")
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model'")
    if not key_may_use_alias(vk, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Key not allowed to use model {model!r}")

    try:
        aliases = await build_candidate_aliases(session, model)
    except RouteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        adapter = get_adapter(dep.provider_type)
        params = _prepare_params(dep, body)
        try:
            req = adapter.build_embedding_request(
                base_url=dep.base_url, api_key=dep.api_key, org=dep.org,
                extra_headers=dep.extra_headers, upstream_model=dep.upstream_model, params=params,
            )
        except NotImplementedError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Provider {dep.provider_type} does not support embeddings")
        try:
            resp = await client.request(req.method, req.url, headers=req.headers, json=req.json,
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
        usage = adapter.extract_usage(data) if data.get("usage") else Usage()
        await log_request(session, virtual_key_id=vk.id, requested_model=model, deployment=dep,
                          usage=usage, status=200, cost=0.0,
                          latency_ms=int((time.monotonic() - started) * 1000), retries=0)
        await session.commit()
        return JSONResponse(data)

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)
