"""Per-request correlation id, propagated via a contextvar.

Set by the X-Request-Id middleware at the edge so any code on the request's
async task (including the request-log payload builder) can stamp the same id,
and the value is echoed back in the response header for client-side tracing.
"""
from __future__ import annotations

import contextvars
import uuid

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
# The authenticated admin identity ("master" or a username), set during auth so
# the audit middleware can attribute the change to the real actor.
_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "actor", default=None
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_actor(value: str | None) -> None:
    _actor.set(value)


def get_actor() -> str | None:
    return _actor.get()
