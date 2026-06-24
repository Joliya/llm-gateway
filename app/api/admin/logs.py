from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.db.models import AdminAuditLog, RequestLog
from app.db.session import get_session

router = APIRouter(dependencies=[Depends(require_admin)])


def _like(term: str) -> str:
    """Escape LIKE wildcards in a user term so it matches literally, then wrap
    it for a contains-match. Paired with `escape="\\"` on the column op."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/logs")
async def list_logs(
    session: AsyncSession = Depends(get_session),
    virtual_key_id: int | None = None,
    alias: str | None = None,
    model: str | None = Query(None, description="Fuzzy match on requested model or alias"),
    provider: str | None = Query(None, description="Exact match on provider name"),
    start: dt.datetime | None = Query(None, description="Only requests at/after this time"),
    end: dt.datetime | None = Query(None, description="Only requests at/before this time"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
):
    stmt = select(RequestLog).order_by(RequestLog.ts.desc())
    if virtual_key_id is not None:
        stmt = stmt.where(RequestLog.virtual_key_id == virtual_key_id)
    if alias is not None:
        stmt = stmt.where(RequestLog.alias == alias)
    if model:
        pat = _like(model.strip())
        stmt = stmt.where(or_(
            RequestLog.requested_model.ilike(pat, escape="\\"),
            RequestLog.alias.ilike(pat, escape="\\"),
        ))
    if provider:
        # Exact match — the console populates this from the known provider list,
        # so it can use an index instead of a substring scan.
        stmt = stmt.where(RequestLog.provider_name == provider)
    if start is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.UTC)
        stmt = stmt.where(RequestLog.ts >= start)
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.UTC)
        stmt = stmt.where(RequestLog.ts <= end)
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [
        {
            "id": r.id, "ts": r.ts, "request_id": r.request_id,
            "virtual_key_id": r.virtual_key_id,
            "requested_model": r.requested_model, "alias": r.alias,
            "deployment_id": r.deployment_id, "provider_name": r.provider_name,
            "credential_id": r.credential_id, "provider_type": r.provider_type,
            "status": r.status, "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens, "total_tokens": r.total_tokens,
            "cost": r.cost, "latency_ms": r.latency_ms, "retries": r.retries,
            "cache_hit": r.cache_hit, "error": r.error,
            "upstream_url": r.upstream_url,
            # Bodies can be large; the list flags their presence, the detail
            # endpoint (`GET /admin/logs/{id}`) returns them in full.
            "has_upstream_io": r.upstream_request is not None or r.upstream_response is not None,
        }
        for r in rows
    ]


@router.get("/logs/{log_id}")
async def get_log(log_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(RequestLog, log_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Log not found")
    return {
        "id": r.id, "ts": r.ts, "request_id": r.request_id,
        "virtual_key_id": r.virtual_key_id,
        "requested_model": r.requested_model, "alias": r.alias,
        "deployment_id": r.deployment_id, "provider_name": r.provider_name,
        "credential_id": r.credential_id, "provider_type": r.provider_type,
        "status": r.status, "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens, "total_tokens": r.total_tokens,
        "cost": r.cost, "latency_ms": r.latency_ms, "retries": r.retries,
        "cache_hit": r.cache_hit, "error": r.error,
        "upstream_url": r.upstream_url,
        "upstream_request": r.upstream_request,
        "upstream_response": r.upstream_response,
    }


@router.get("/usage")
async def usage_summary(
    session: AsyncSession = Depends(get_session),
    since_hours: int = Query(24, ge=1),
):
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=since_hours)
    stmt = (
        select(
            RequestLog.alias,
            func.count(RequestLog.id),
            func.sum(RequestLog.total_tokens),
            func.sum(RequestLog.cost),
            func.avg(RequestLog.latency_ms),
        )
        .where(RequestLog.ts >= since)
        .group_by(RequestLog.alias)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "alias": alias, "requests": count, "total_tokens": int(tokens or 0),
            "cost": float(cost or 0.0), "avg_latency_ms": round(float(avg or 0.0), 1),
        }
        for alias, count, tokens, cost, avg in rows
    ]


@router.get("/usage/by-key")
async def usage_by_key(
    session: AsyncSession = Depends(get_session),
    since_hours: int = Query(24, ge=1),
):
    """Consumption grouped by virtual key (for the console analytics view)."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=since_hours)
    stmt = (
        select(
            RequestLog.virtual_key_id,
            func.count(RequestLog.id),
            func.sum(RequestLog.total_tokens),
            func.sum(RequestLog.cost),
        )
        .where(RequestLog.ts >= since)
        .group_by(RequestLog.virtual_key_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "virtual_key_id": vk_id, "requests": count,
            "total_tokens": int(tokens or 0), "cost": float(cost or 0.0),
        }
        for vk_id, count, tokens, cost in rows
    ]


@router.get("/audit")
async def list_audit(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, le=1000),
    offset: int = 0,
):
    """Recent mutating /admin calls (most recent first)."""
    stmt = select(AdminAuditLog).order_by(AdminAuditLog.ts.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id, "ts": r.ts, "request_id": r.request_id, "actor": r.actor,
            "method": r.method, "path": r.path, "status": r.status,
        }
        for r in rows
    ]
