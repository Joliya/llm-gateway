from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.config_store import config_store
from app.db.models import Alias, Credential, Deployment
from app.db.session import get_session
from app.schemas.admin import DeploymentCreate, DeploymentOut, DeploymentUpdate

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(Deployment))).scalars().all()


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def create_deployment(payload: DeploymentCreate, session: AsyncSession = Depends(get_session)):
    if await session.get(Alias, payload.alias_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "alias_id does not exist")
    if await session.get(Credential, payload.credential_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "credential_id does not exist")
    dep = Deployment(**payload.model_dump())
    session.add(dep)
    await session.commit()
    await session.refresh(dep)
    config_store.invalidate()
    return dep


@router.patch("/{deployment_id}", response_model=DeploymentOut)
async def update_deployment(deployment_id: int, payload: DeploymentUpdate,
                            session: AsyncSession = Depends(get_session)):
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(dep, k, v)
    await session.commit()
    await session.refresh(dep)
    config_store.invalidate()
    return dep


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(deployment_id: int, session: AsyncSession = Depends(get_session)):
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    await session.delete(dep)
    await session.commit()
    config_store.invalidate()
