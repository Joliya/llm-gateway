from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.auth import require_master
from app.core.security import (
    generate_password,
    hash_password,
    issue_session_token,
    verify_password,
)
from app.db.models import User
from app.db.session import get_session
from app.schemas.admin import (
    LoginRequest,
    LoginResponse,
    UserCreate,
    UserCreated,
    UserOut,
    UserUpdate,
)

_settings = get_settings()

# Login is intentionally unauthenticated (it's how a user obtains a session).
login_router = APIRouter()


@login_router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    # Verify even when the user is missing/disabled to avoid leaking which is which.
    valid = user is not None and user.enabled and verify_password(payload.password, user.password_hash)
    if not valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    ttl = int(_settings.session_ttl_hours * 3600)
    token, exp = issue_session_token(user.id, ttl)
    user.last_login_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return LoginResponse(
        token=token,
        expires_at=dt.datetime.fromtimestamp(exp, dt.UTC),
        username=user.username,
    )


# User management is master-only — a logged-in user cannot manage other users.
router = APIRouter(dependencies=[Depends(require_master)])


@router.get("", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(User).order_by(User.id))).scalars().all()


@router.post("", response_model=UserCreated, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    password = generate_password()
    user = User(username=payload.username, password_hash=hash_password(password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists") from None
    await session.refresh(user)
    base = UserOut.model_validate(user, from_attributes=True)
    return UserCreated(**base.model_dump(), password=password)  # shown once


@router.post("/{user_id}/reset-password", response_model=UserCreated)
async def reset_password(user_id: int, session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    password = generate_password()
    user.password_hash = hash_password(password)
    await session.commit()
    await session.refresh(user)
    base = UserOut.model_validate(user, from_attributes=True)
    return UserCreated(**base.model_dump(), password=password)  # shown once


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, payload: UserUpdate, session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await session.delete(user)
    await session.commit()
