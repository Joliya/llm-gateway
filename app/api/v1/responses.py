from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._common import (
    OPENAI_TYPES,
    json_passthrough,
    openai_headers,
    resolve_aliases,
    upstream_base,
)
from app.config import get_settings
from app.core.auth import authenticate_virtual_key
from app.core.circuit_breaker import circuit_breaker
from app.core.cost import compute_cost, usage_from_openai
from app.core.executor import _iter_attempts, _prepare_params
from app.core.logging_service import log_request
from app.db.models import VirtualKey
from app.db.session import get_session
from app.providers.base import Usage

router = APIRouter()
_settings = get_settings()


@router.post("/responses")
async def responses(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """OpenAI Responses API. Proxied to openai-compatible upstreams with the same
    routing / load-balancing / fallback as chat. Supports streaming."""
    body: dict[str, Any] = await request.json()
    model = body.get("model")
    if body.get("stream"):
        return await _stream(request, session, vk, body, model)
    return await json_passthrough(
        request=request, session=session, vk=vk, body=body, model=model,
        subpath="responses", label="/responses",
    )


def _usage_from_stream(text: str) -> Usage:
    """Best-effort: pull usage from the terminal response.completed SSE event."""
    found: Usage | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        resp = evt.get("response") if isinstance(evt, dict) else None
        if isinstance(resp, dict) and resp.get("usage"):
            found = usage_from_openai(resp)
    return found or Usage()


async def _stream(request: Request, session: AsyncSession, vk: VirtualKey, body: dict, model):
    aliases = await resolve_aliases(session, model, vk)
    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        if dep.provider_type not in OPENAI_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"/responses not supported for provider {dep.provider_type}")
        params = _prepare_params(dep, body)
        params["model"] = dep.upstream_model
        params["stream"] = True
        url = f"{upstream_base(dep)}/responses"
        req = client.build_request("POST", url, headers=openai_headers(dep), json=params,
                                   timeout=_settings.request_timeout)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            circuit_breaker.record_failure(dep.deployment_id)
            last_status, last_body = 502, str(exc)
            continue
        if resp.status_code >= 400:
            err = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            if resp.status_code == 429 or resp.status_code >= 500:
                circuit_breaker.record_failure(dep.deployment_id)
                last_status, last_body = resp.status_code, err
                continue
            return JSONResponse({"error": {"message": err}}, status_code=resp.status_code)

        circuit_breaker.record_success(dep.deployment_id)

        async def event_stream(dep=dep, resp=resp, url=url, params=params):
            buf: list[str] = []
            try:
                async for raw in resp.aiter_bytes():
                    buf.append(raw.decode("utf-8", "replace"))
                    yield raw
            finally:
                await resp.aclose()
                usage = _usage_from_stream("".join(buf))
                cost = compute_cost(usage, dep.input_price, dep.output_price)
                await log_request(session, virtual_key_id=vk.id, requested_model=model,
                                  deployment=dep, usage=usage, status=200, cost=cost,
                                  latency_ms=int((time.monotonic() - started) * 1000), retries=0,
                                  upstream_url=url, upstream_request=params,
                                  upstream_response="[streamed]")
                await session.commit()

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"cache-control": "no-cache"})

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)
