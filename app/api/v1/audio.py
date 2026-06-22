from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._common import (
    OPENAI_TYPES,
    openai_headers,
    resolve_aliases,
    upstream_base,
)
from app.config import get_settings
from app.core.auth import authenticate_virtual_key
from app.core.circuit_breaker import circuit_breaker
from app.core.cost import compute_cost, cost_headers, usage_from_openai
from app.core.executor import _iter_attempts, _prepare_params
from app.core.logging_service import log_request
from app.db.models import VirtualKey
from app.db.session import get_session
from app.providers.base import Usage

router = APIRouter()
_settings = get_settings()


@router.post("/audio/transcriptions")
async def transcriptions(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """Speech-to-text (Whisper / gpt-4o-transcribe). Multipart upload proxied to
    openai-compatible upstreams. Returns JSON or text per response_format."""
    form = await request.form()
    model = form.get("model")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'file'")
    file_bytes = await upload.read()  # read once; reused across fallback attempts
    filename = getattr(upload, "filename", "audio") or "audio"
    file_ctype = getattr(upload, "content_type", None) or "application/octet-stream"
    # Other simple form fields (language, prompt, response_format, temperature…).
    extra: dict[str, str] = {
        k: v for k, v in form.multi_items()
        if k not in ("model", "file") and isinstance(v, str)
    }

    aliases = await resolve_aliases(session, model, vk)
    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        if dep.provider_type not in OPENAI_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"/audio/transcriptions not supported for provider {dep.provider_type}")
        data = dict(extra)
        data["model"] = dep.upstream_model
        files = {"file": (filename, file_bytes, file_ctype)}
        url = f"{upstream_base(dep)}/audio/transcriptions"
        try:
            resp = await client.post(url, headers=openai_headers(dep, json_body=False),
                                     data=data, files=files, timeout=_settings.request_timeout)
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
        ctype = resp.headers.get("content-type", "")
        is_json = "application/json" in ctype
        payload = resp.json() if is_json else resp.text
        usage = usage_from_openai(payload) if is_json else Usage()
        cost = compute_cost(usage, dep.input_price, dep.output_price)
        req_meta = {"model": dep.upstream_model, "file": filename, **extra}
        await log_request(session, virtual_key_id=vk.id, requested_model=model, deployment=dep,
                          usage=usage, status=200, cost=cost,
                          latency_ms=int((time.monotonic() - started) * 1000), retries=0,
                          upstream_url=url, upstream_request=req_meta,
                          upstream_response=payload)
        await session.commit()
        if is_json:
            return JSONResponse(payload, headers=cost_headers(cost))
        return Response(payload, media_type=ctype or "text/plain", headers=cost_headers(cost))

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)


@router.post("/audio/speech")
async def speech(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """Text-to-speech (tts-1 / gpt-4o-mini-tts). JSON in, binary audio out."""
    body: dict[str, Any] = await request.json()
    model = body.get("model")
    aliases = await resolve_aliases(session, model, vk)
    client: httpx.AsyncClient = request.app.state.http_client
    started = time.monotonic()
    last_status, last_body = 502, "no upstream available"

    for dep in _iter_attempts(aliases):
        if dep.provider_type not in OPENAI_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"/audio/speech not supported for provider {dep.provider_type}")
        params = _prepare_params(dep, body)
        params["model"] = dep.upstream_model
        url = f"{upstream_base(dep)}/audio/speech"
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
        audio = resp.content
        await log_request(session, virtual_key_id=vk.id, requested_model=model, deployment=dep,
                          usage=Usage(), status=200, cost=0.0,
                          latency_ms=int((time.monotonic() - started) * 1000), retries=0,
                          upstream_url=url, upstream_request=params, upstream_response="[audio]")
        await session.commit()
        return Response(content=audio,
                        media_type=resp.headers.get("content-type", "audio/mpeg"),
                        headers=cost_headers(0.0))

    return JSONResponse({"error": {"message": last_body}}, status_code=last_status)
