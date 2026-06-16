from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import require_master_key
from app.core.circuit_breaker import circuit_breaker

router = APIRouter(dependencies=[Depends(require_master_key)])


@router.get("/deployment-health")
async def deployment_health():
    """Circuit-breaker state per deployment id (only those with recorded events)."""
    return {"deployments": circuit_breaker.status()}
