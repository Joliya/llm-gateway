"""Edge middleware: request-id propagation + admin audit logging.

Implemented as raw ASGI (not Starlette's BaseHTTPMiddleware) so the request-id
contextvar set here propagates into the endpoint coroutine — BaseHTTPMiddleware
runs the endpoint in a separate task and would lose it.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.core.request_context import new_request_id, set_request_id

_settings = get_settings()
_log = logging.getLogger("llm_gateway.audit")

_MUTATING = {"POST", "PATCH", "PUT", "DELETE"}


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw_id = headers.get(b"x-request-id")
        request_id = raw_id.decode("latin-1")[:64] if raw_id else new_request_id()
        set_request_id(request_id)

        status_code = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code["code"] = message["status"]
                resp_headers = list(message.get("headers") or [])
                resp_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = resp_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

        method = scope.get("method", "")
        path = scope.get("path", "")
        if (
            _settings.admin_audit_enabled
            and method in _MUTATING
            and path.startswith("/admin")
        ):
            actor = headers.get(b"x-admin-actor")
            await self._audit(
                request_id,
                actor.decode("latin-1")[:150] if actor else None,
                method,
                path,
                status_code["code"],
            )

    async def _audit(self, request_id, actor, method, path, status) -> None:
        # Never let an audit-write failure affect the already-sent response.
        try:
            from app.db.models import AdminAuditLog
            from app.db.session import SessionLocal

            async with SessionLocal() as session:
                session.add(
                    AdminAuditLog(
                        request_id=request_id, actor=actor,
                        method=method, path=path, status=status,
                    )
                )
                await session.commit()
        except Exception:
            _log.exception("admin audit write failed (%s %s)", method, path)
