from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import metrics
from app.core.config_store import ResolvedDeployment
from app.core.request_context import get_request_id
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.providers.base import Usage

_settings = get_settings()
_log = logging.getLogger("llm_gateway.logging")


def _capture(value: Any) -> Any:
    """Honor the GW_LOG_UPSTREAM_IO toggle and cap oversized string payloads."""
    if not _settings.log_upstream_io or value is None:
        return None
    cap = _settings.log_upstream_max_chars
    if cap and isinstance(value, str) and len(value) > cap:
        return value[:cap] + f"\n…[truncated {len(value) - cap} chars]"
    return value


def _build_payload(
    *,
    virtual_key_id: int | None,
    requested_model: str,
    deployment: ResolvedDeployment | None,
    usage: Usage | None,
    status: int,
    cost: float,
    latency_ms: int,
    retries: int,
    cache_hit: bool,
    error: str | None,
    upstream_url: str | None,
    upstream_request: dict[str, Any] | None,
    upstream_response: Any,
) -> dict[str, Any]:
    """Map call args to RequestLog column values (plain, serializable dict)."""
    usage = usage or Usage()
    return {
        "request_id": get_request_id(),
        "virtual_key_id": virtual_key_id,
        "requested_model": requested_model,
        "alias": deployment.alias_name if deployment else None,
        "deployment_id": (
            deployment.deployment_id if deployment and deployment.deployment_id > 0 else None
        ),
        "provider_type": deployment.provider_type if deployment else None,
        "status": status,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost": cost,
        "latency_ms": latency_ms,
        "retries": retries,
        "cache_hit": cache_hit,
        "error": error,
        "upstream_url": upstream_url,
        "upstream_request": _capture(upstream_request),
        "upstream_response": _capture(upstream_response),
    }


class RequestLogger:
    """Off-path request-log writer.

    When enabled, `enqueue` drops a payload on a bounded in-memory queue and a
    background task batches them into the DB on its own session, so the request
    path never waits on the log insert. A full queue drops the row (and counts
    it) rather than blocking — request latency always wins, and the dropped rows
    are observability data only (billing `spend` is committed synchronously
    elsewhere). Disabled for SQLite / when GW_LOG_ASYNC=false: callers fall back
    to a synchronous inline write on the request session.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._enabled = False
        self.dropped = 0
        self.write_errors = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0

    async def start(self) -> None:
        # SQLite can't take concurrent writers well; keep it inline.
        if not _settings.log_async or _settings.database_url.startswith("sqlite"):
            self._enabled = False
            return
        self._queue = asyncio.Queue(maxsize=_settings.log_queue_max)
        self._stopping = False
        self._task = asyncio.create_task(self._worker())
        self._enabled = True

    async def stop(self) -> None:
        if not self._enabled or self._task is None:
            return
        self._stopping = True
        if self._queue is not None:
            # nudge the worker out of its get() wait so it can see _stopping
            try:
                self._queue.put_nowait({})  # sentinel; skipped by _write_batch
            except asyncio.QueueFull:
                pass
        try:
            await self._task
        finally:
            self._enabled = False

    def enqueue(self, payload: dict[str, Any]) -> bool:
        if not self._enabled or self._queue is None:
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    async def drain(self) -> None:
        """Block until everything enqueued so far has been written (tests)."""
        if self._queue is not None:
            await self._queue.join()

    async def _worker(self) -> None:
        assert self._queue is not None
        q = self._queue
        while True:
            batch: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(q.get(), timeout=_settings.log_flush_interval)
                batch.append(first)
            except TimeoutError:
                first = None
            while len(batch) < _settings.log_batch_size:
                try:
                    batch.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if batch:
                rows = [p for p in batch if p]  # drop sentinels/empties
                if rows:
                    try:
                        await self._write_batch(rows)
                    except Exception:  # never let the worker die on a write error
                        self.write_errors += 1
                        _log.exception("request-log batch write failed (%d rows)", len(rows))
                for _ in batch:
                    q.task_done()
            if self._stopping and q.empty():
                break

    async def _write_batch(self, rows: list[dict[str, Any]]) -> None:
        async with SessionLocal() as session:
            session.add_all([RequestLog(**r) for r in rows])
            await session.commit()


request_logger = RequestLogger()


async def log_request(
    session: AsyncSession,
    *,
    virtual_key_id: int | None,
    requested_model: str,
    deployment: ResolvedDeployment | None,
    usage: Usage | None,
    status: int,
    cost: float,
    latency_ms: int,
    retries: int,
    cache_hit: bool = False,
    error: str | None = None,
    upstream_url: str | None = None,
    upstream_request: dict[str, Any] | None = None,
    upstream_response: Any = None,
) -> None:
    """Record one proxied request. Enqueues for async write when enabled,
    otherwise writes inline on the caller's session (committed by the caller)."""
    payload = _build_payload(
        virtual_key_id=virtual_key_id,
        requested_model=requested_model,
        deployment=deployment,
        usage=usage,
        status=status,
        cost=cost,
        latency_ms=latency_ms,
        retries=retries,
        cache_hit=cache_hit,
        error=error,
        upstream_url=upstream_url,
        upstream_request=upstream_request,
        upstream_response=upstream_response,
    )
    metrics.record_request(
        alias=payload["alias"],
        provider_type=payload["provider_type"],
        status=payload["status"],
        cost=payload["cost"],
        prompt_tokens=payload["prompt_tokens"],
        completion_tokens=payload["completion_tokens"],
        latency_ms=payload["latency_ms"],
        cache_hit=payload["cache_hit"],
    )
    if request_logger.enabled:
        request_logger.enqueue(payload)
        return
    session.add(RequestLog(**payload))
    await session.flush()
