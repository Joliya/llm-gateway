from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import budget as budget_mod
from app.core.auth import authenticate_virtual_key, key_may_use_alias
from app.core.cache import cache_enabled_for, make_cache_key, response_cache
from app.core.cost import compute_cost
from app.core.executor import AllAttemptsFailed, ChatExecutor, build_candidate_aliases
from app.core.logging_service import log_request
from app.core.router import RouteNotFound
from app.db.models import VirtualKey
from app.db.session import get_session
from app.providers.base import Usage

router = APIRouter()


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    body: dict[str, Any] = await request.json()
    model = body.get("model")
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model'")
    is_stream = bool(body.get("stream"))

    if not key_may_use_alias(vk, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Key not allowed to use model {model!r}")

    await budget_mod.reset_if_needed(session, vk)
    if budget_mod.is_over_budget(vk):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Budget exceeded for this key")
    await session.commit()

    try:
        aliases = await build_candidate_aliases(session, model)
    except RouteNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    primary = aliases[0]
    client = request.app.state.http_client
    executor = ChatExecutor(session, client, vk.id, vk.rpm_limit, vk.tpm_limit)
    started = time.monotonic()

    # --- cache (non-stream only) ---
    use_cache = (not is_stream) and cache_enabled_for(primary.cache_enabled)
    cache_key = make_cache_key(model, body) if use_cache else None
    if cache_key:
        cached = await response_cache.get(cache_key)
        if cached is not None:
            await log_request(
                session, virtual_key_id=vk.id, requested_model=model, deployment=None,
                usage=Usage(), status=200, cost=0.0,
                latency_ms=int((time.monotonic() - started) * 1000), retries=0, cache_hit=True,
            )
            await session.commit()
            return JSONResponse(cached)

    if is_stream:
        return await _stream_response(executor, aliases, body, model, vk, session, started)

    try:
        result = await executor.run(aliases, body)
    except AllAttemptsFailed as exc:
        await log_request(
            session, virtual_key_id=vk.id, requested_model=model, deployment=exc.deployment, usage=Usage(),
            status=exc.status_code, cost=0.0,
            latency_ms=int((time.monotonic() - started) * 1000), retries=0, error=exc.body[:1000],
            upstream_url=exc.upstream_url, upstream_request=exc.upstream_request,
            upstream_response=exc.upstream_response,
        )
        await session.commit()
        return JSONResponse(
            {"error": {"message": exc.body, "type": "upstream_error", "code": exc.status_code}},
            status_code=exc.status_code,
        )

    cost = compute_cost(result.usage, result.deployment.input_price, result.deployment.output_price)
    await budget_mod.add_spend(session, vk, cost)
    await log_request(
        session, virtual_key_id=vk.id, requested_model=model, deployment=result.deployment,
        usage=result.usage, status=200, cost=cost,
        latency_ms=int((time.monotonic() - started) * 1000), retries=result.retries,
        upstream_url=result.upstream_url, upstream_request=result.upstream_request,
        upstream_response=result.upstream_response,
    )
    await session.commit()

    if cache_key:
        await response_cache.set(cache_key, result.response)

    return JSONResponse(result.response)


async def _stream_response(executor, aliases, body, model, vk, session, started):
    try:
        stream_result = await executor.run_stream(aliases, body)
    except AllAttemptsFailed as exc:
        await log_request(
            session, virtual_key_id=vk.id, requested_model=model, deployment=exc.deployment, usage=Usage(),
            status=exc.status_code, cost=0.0,
            latency_ms=int((time.monotonic() - started) * 1000), retries=0, error=exc.body[:1000],
            upstream_url=exc.upstream_url, upstream_request=exc.upstream_request,
            upstream_response=exc.upstream_response,
        )
        await session.commit()
        return JSONResponse(
            {"error": {"message": exc.body, "type": "upstream_error", "code": exc.status_code}},
            status_code=exc.status_code,
        )

    async def event_stream():
        try:
            async for chunk in stream_result.chunks:
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            cost = compute_cost(
                stream_result.usage,
                stream_result.deployment.input_price,
                stream_result.deployment.output_price,
            )
            await budget_mod.add_spend(session, vk, cost)
            await log_request(
                session, virtual_key_id=vk.id, requested_model=model,
                deployment=stream_result.deployment, usage=stream_result.usage, status=200,
                cost=cost, latency_ms=int((time.monotonic() - started) * 1000),
                retries=stream_result.retries,
                upstream_url=stream_result.upstream_url, upstream_request=stream_result.upstream_request,
                upstream_response="[streamed]",
            )
            await session.commit()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
