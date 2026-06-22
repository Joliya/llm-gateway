from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._common import json_passthrough
from app.core.auth import authenticate_virtual_key
from app.db.models import VirtualKey
from app.db.session import get_session

router = APIRouter()


@router.post("/images/generations")
async def images_generations(
    request: Request,
    vk: VirtualKey = Depends(authenticate_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    """OpenAI image generation. Proxied to openai-compatible upstreams. Cost is
    derived from token usage when the model returns it (e.g. gpt-image-1);
    otherwise logged as 0 (dall-e bills per image, not per token)."""
    body: dict[str, Any] = await request.json()
    return await json_passthrough(
        request=request, session=session, vk=vk, body=body, model=body.get("model"),
        subpath="images/generations", label="/images/generations",
    )
