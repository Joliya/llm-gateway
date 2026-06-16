from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import authenticate_virtual_key
from app.core.config_store import config_store
from app.db.models import VirtualKey
from app.db.session import get_session

router = APIRouter()


@router.get("/models")
async def list_models(
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """OpenAI-compatible model list: exposes configured aliases this key may use."""
    snapshot = await config_store.get(session)
    allowed = vk.allowed_aliases or ["*"]
    wildcard = "*" in allowed
    created = int(time.time())
    data = [
        {"id": name, "object": "model", "created": created, "owned_by": "llm-gateway"}
        for name in sorted(snapshot.aliases)
        if (wildcard or name in allowed) and snapshot.aliases[name].deployments
    ]
    return {"object": "list", "data": data}
