from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.config_store import ResolvedAlias, config_store
from app.core.cost import compute_cost
from app.core.executor import AllAttemptsFailed, ChatExecutor, build_candidate_aliases
from app.core.router import RouteNotFound
from app.db.session import get_session

router = APIRouter(dependencies=[Depends(require_admin)])


class PlaygroundRequest(BaseModel):
    model: str | None = None
    deployment_id: int | None = None
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None


async def _resolve_playground_aliases(
    session: AsyncSession,
    payload: PlaygroundRequest,
) -> list[ResolvedAlias] | JSONResponse:
    has_model = bool(payload.model and payload.model.strip())
    has_deployment = payload.deployment_id is not None
    if has_model == has_deployment:
        return JSONResponse(
            {"error": {"message": "Provide exactly one of model or deployment_id", "type": "bad_request"}},
            status_code=400,
        )

    if has_model:
        try:
            return await build_candidate_aliases(session, payload.model.strip())
        except RouteNotFound as exc:
            return JSONResponse({"error": {"message": str(exc), "type": "route_not_found"}}, status_code=404)

    snapshot = await config_store.get(session)
    for alias in snapshot.aliases.values():
        for dep in alias.deployments:
            if dep.deployment_id == payload.deployment_id:
                return [
                    ResolvedAlias(
                        name=alias.name,
                        lb_strategy="round_robin",
                        fallback_aliases=[],
                        cache_enabled=alias.cache_enabled,
                        deployments=[dep],
                    )
                ]
    return JSONResponse(
        {
            "error": {
                "message": f"Deployment {payload.deployment_id!r} is not available",
                "type": "route_not_found",
            }
        },
        status_code=404,
    )


@router.post("/playground/chat")
async def playground_chat(
    payload: PlaygroundRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run a non-streaming chat completion through the real routing path,
    authenticated by the master key (no virtual key / budget needed). Returns
    the response plus which deployment served it — a routing test bench."""
    aliases = await _resolve_playground_aliases(session, payload)
    if isinstance(aliases, JSONResponse):
        return aliases

    model = payload.model.strip() if payload.model else aliases[0].name
    body: dict[str, Any] = {"model": model, "messages": payload.messages}
    if payload.temperature is not None:
        body["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        body["max_tokens"] = payload.max_tokens

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
