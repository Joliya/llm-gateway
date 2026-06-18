from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_master_key
from app.core.cost import compute_cost
from app.core.executor import AllAttemptsFailed, ChatExecutor, build_candidate_aliases
from app.core.router import RouteNotFound
from app.db.session import get_session

router = APIRouter(dependencies=[Depends(require_master_key)])


class PlaygroundRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None


@router.post("/playground/chat")
async def playground_chat(
    payload: PlaygroundRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run a non-streaming chat completion through the real routing path,
    authenticated by the master key (no virtual key / budget needed). Returns
    the response plus which deployment served it — a routing test bench."""
    body: dict[str, Any] = {"model": payload.model, "messages": payload.messages}
    if payload.temperature is not None:
        body["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        body["max_tokens"] = payload.max_tokens

    try:
        aliases = await build_candidate_aliases(session, payload.model)
    except RouteNotFound as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "route_not_found"}}, status_code=404)

    executor = ChatExecutor(session, request.app.state.http_client, None, None, None)
    started = time.monotonic()
    try:
        result = await executor.run(aliases, body)
    except AllAttemptsFailed as exc:
        return JSONResponse(
            {"error": {"message": exc.body, "type": "upstream_error", "code": exc.status_code}},
            status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    dep = result.deployment
    cost = compute_cost(result.usage, dep.input_price, dep.output_price)
    content = ""
    try:
        content = result.response["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError):
        pass

    return {
        "content": content,
        "raw": result.response,
        "meta": {
            "alias": dep.alias_name,
            "deployment_id": dep.deployment_id if dep.deployment_id > 0 else None,
            "provider_type": dep.provider_type,
            "upstream_model": dep.upstream_model,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
            "cost": round(cost, 6),
            "latency_ms": latency_ms,
            "retries": result.retries,
        },
    }
