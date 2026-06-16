from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_master_key
from app.core.config_store import config_store
from app.db.models import Provider
from app.db.session import get_session
from app.providers.registry import supported_provider_types
from app.schemas.admin import ProviderCreate, ProviderOut, ProviderUpdate

router = APIRouter(dependencies=[Depends(require_master_key)])


@router.get("/provider-types")
async def provider_types():
    return {"provider_types": supported_provider_types()}


@router.get("", response_model=list[ProviderOut])
async def list_providers(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Provider))).scalars().all()
    return rows


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(payload: ProviderCreate, session: AsyncSession = Depends(get_session)):
    if payload.provider_type not in supported_provider_types():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unsupported provider_type. Allowed: {supported_provider_types()}")
    provider = Provider(**payload.model_dump())
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    config_store.invalidate()
    return provider


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: int, payload: ProviderUpdate,
                          session: AsyncSession = Depends(get_session)):
    provider = await session.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(provider, k, v)
    await session.commit()
    await session.refresh(provider)
    config_store.invalidate()
    return provider


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: int, session: AsyncSession = Depends(get_session)):
    provider = await session.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    await session.delete(provider)
    await session.commit()
    config_store.invalidate()
