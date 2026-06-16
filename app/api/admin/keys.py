from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_master_key
from app.core.security import generate_virtual_key, hash_key, key_display_prefix
from app.db.models import VirtualKey
from app.db.session import get_session
from app.schemas.admin import VirtualKeyCreate, VirtualKeyCreated, VirtualKeyOut, VirtualKeyUpdate

router = APIRouter(dependencies=[Depends(require_master_key)])


@router.get("", response_model=list[VirtualKeyOut])
async def list_keys(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(VirtualKey))).scalars().all()


@router.post("", response_model=VirtualKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(payload: VirtualKeyCreate, session: AsyncSession = Depends(get_session)):
    plaintext = generate_virtual_key()
    vk = VirtualKey(
        key_hash=hash_key(plaintext),
        key_prefix=key_display_prefix(plaintext),
        **payload.model_dump(),
    )
    session.add(vk)
    await session.commit()
    await session.refresh(vk)
    base = VirtualKeyOut.model_validate(vk, from_attributes=True)
    return VirtualKeyCreated(**base.model_dump(), key=plaintext)  # key shown once


@router.patch("/{key_id}", response_model=VirtualKeyOut)
async def update_key(key_id: int, payload: VirtualKeyUpdate, session: AsyncSession = Depends(get_session)):
    vk = await session.get(VirtualKey, key_id)
    if vk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(vk, k, v)
    await session.commit()
    await session.refresh(vk)
    return vk


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(key_id: int, session: AsyncSession = Depends(get_session)):
    vk = await session.get(VirtualKey, key_id)
    if vk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    await session.delete(vk)
    await session.commit()
