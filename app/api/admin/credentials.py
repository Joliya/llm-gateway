from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.config_store import config_store
from app.core.security import encrypt_secret
from app.db.models import Credential, Provider
from app.db.session import get_session
from app.schemas.admin import CredentialCreate, CredentialOut, CredentialUpdate

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("", response_model=list[CredentialOut])
async def list_credentials(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(Credential))).scalars().all()


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def create_credential(payload: CredentialCreate, session: AsyncSession = Depends(get_session)):
    if await session.get(Provider, payload.provider_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider_id does not exist")
    data = payload.model_dump()
    api_key = data.pop("api_key")
    cred = Credential(api_key_enc=encrypt_secret(api_key), **data)
    session.add(cred)
    await session.commit()
    await session.refresh(cred)
    config_store.invalidate()
    return cred


@router.patch("/{credential_id}", response_model=CredentialOut)
async def update_credential(credential_id: int, payload: CredentialUpdate,
                            session: AsyncSession = Depends(get_session)):
    cred = await session.get(Credential, credential_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data:
        cred.api_key_enc = encrypt_secret(data.pop("api_key"))
    for k, v in data.items():
        setattr(cred, k, v)
    await session.commit()
    await session.refresh(cred)
    config_store.invalidate()
    return cred


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: int, session: AsyncSession = Depends(get_session)):
    cred = await session.get(Credential, credential_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    await session.delete(cred)
    await session.commit()
    config_store.invalidate()
