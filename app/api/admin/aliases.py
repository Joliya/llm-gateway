from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_master_key
from app.core.config_store import config_store
from app.db.models import Alias
from app.db.session import get_session
from app.schemas.admin import AliasCreate, AliasOut, AliasUpdate

router = APIRouter(dependencies=[Depends(require_master_key)])

_STRATEGIES = {"round_robin", "weighted", "least_busy", "random"}


@router.get("", response_model=list[AliasOut])
async def list_aliases(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(Alias))).scalars().all()


@router.post("", response_model=AliasOut, status_code=status.HTTP_201_CREATED)
async def create_alias(payload: AliasCreate, session: AsyncSession = Depends(get_session)):
    if payload.lb_strategy not in _STRATEGIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"lb_strategy must be one of {_STRATEGIES}")
    alias = Alias(**payload.model_dump())
    session.add(alias)
    await session.commit()
    await session.refresh(alias)
    config_store.invalidate()
    return alias


@router.patch("/{alias_id}", response_model=AliasOut)
async def update_alias(alias_id: int, payload: AliasUpdate, session: AsyncSession = Depends(get_session)):
    alias = await session.get(Alias, alias_id)
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    data = payload.model_dump(exclude_unset=True)
    if "lb_strategy" in data and data["lb_strategy"] not in _STRATEGIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"lb_strategy must be one of {_STRATEGIES}")
    for k, v in data.items():
        setattr(alias, k, v)
    await session.commit()
    await session.refresh(alias)
    config_store.invalidate()
    return alias


@router.delete("/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(alias_id: int, session: AsyncSession = Depends(get_session)):
    alias = await session.get(Alias, alias_id)
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    await session.delete(alias)
    await session.commit()
    config_store.invalidate()
