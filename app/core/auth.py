from __future__ import annotations

import datetime as dt

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.request_context import set_actor
from app.core.security import hash_key, verify_session_token
from app.db.models import User, VirtualKey
from app.db.session import get_session

_settings = get_settings()


async def require_admin(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Protect the /admin API. Accepts either the master key or a logged-in
    user's session token, and records who the actor is for the audit log.
    Returns the actor identity ("master" or the username)."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing credentials")

    if token == _settings.master_key:
        set_actor("master")
        return "master"

    uid = verify_session_token(token)
    if uid is not None:
        user = await session.get(User, uid)
        if user is not None and user.enabled:
            set_actor(user.username)
            return user.username

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired credentials")


def require_master(authorization: str | None = Header(default=None)) -> str:
    """Stricter guard for master-only operations (e.g. user management): the
    master key, never a user session. Logged-in users are forbidden."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing credentials")
    if token != _settings.master_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User management requires the master key")
    set_actor("master")
    return "master"


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


async def authenticate_virtual_key(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> VirtualKey:
    """Resolve and validate the caller's virtual key for /v1 endpoints."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key")

    vk = (
        await session.execute(select(VirtualKey).where(VirtualKey.key_hash == hash_key(token)))
    ).scalar_one_or_none()

    if vk is None or not vk.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")

    if vk.expires_at is not None:
        expires = vk.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.UTC)
        if expires < dt.datetime.now(dt.UTC):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")

    return vk


def key_may_use_alias(vk: VirtualKey, alias_name: str) -> bool:
    allowed = vk.allowed_aliases or ["*"]
    if "*" in allowed:
        return True
    # For provider/model form, allow if the bare provider prefix is allowed too.
    if alias_name in allowed:
        return True
    prefix = alias_name.split("/", 1)[0]
    return prefix in allowed
