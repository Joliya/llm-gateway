from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_secret
from app.db.models import Alias, Credential, Deployment, Provider

_settings = get_settings()


@dataclass
class ResolvedDeployment:
    """A flattened, ready-to-call deployment (secrets decrypted)."""

    deployment_id: int
    alias_name: str
    provider_type: str
    upstream_model: str
    base_url: Optional[str]
    api_key: str
    org: Optional[str]
    extra_headers: dict[str, str]
    weight: int
    rpm_limit: Optional[int]
    tpm_limit: Optional[int]
    cred_rpm_limit: Optional[int]
    cred_tpm_limit: Optional[int]
    credential_id: int
    pinned_params: dict[str, Any]
    default_params: dict[str, Any]
    drop_params: list[str]
    input_price: float
    output_price: float


@dataclass
class ResolvedAlias:
    name: str
    lb_strategy: str
    fallback_aliases: list[str]
    cache_enabled: Optional[bool]
    deployments: list[ResolvedDeployment] = field(default_factory=list)


@dataclass
class Snapshot:
    aliases: dict[str, ResolvedAlias]
    # provider name -> (provider_type, default_base_url, [credentials])
    providers_by_name: dict[str, Provider]
    provider_creds: dict[int, list[Credential]]
    loaded_at: float


class ConfigStore:
    """Loads DB config into an in-memory snapshot with a short TTL so that the
    hot proxy path avoids a DB round-trip per request while still picking up
    admin changes within `config_cache_ttl_seconds`."""

    def __init__(self) -> None:
        self._snapshot: Snapshot | None = None
        self._lock = asyncio.Lock()
        self._ttl = _settings.config_cache_ttl_seconds

    def invalidate(self) -> None:
        self._snapshot = None

    async def get(self, session: AsyncSession) -> Snapshot:
        snap = self._snapshot
        if snap is not None and (time.monotonic() - snap.loaded_at) < self._ttl:
            return snap
        async with self._lock:
            snap = self._snapshot
            if snap is not None and (time.monotonic() - snap.loaded_at) < self._ttl:
                return snap
            self._snapshot = await self._load(session)
            return self._snapshot

    async def _load(self, session: AsyncSession) -> Snapshot:
        providers = (await session.execute(select(Provider))).scalars().all()
        credentials = (await session.execute(select(Credential))).scalars().all()
        aliases = (await session.execute(select(Alias))).scalars().all()
        deployments = (await session.execute(select(Deployment))).scalars().all()

        prov_by_id = {p.id: p for p in providers}
        cred_by_id = {c.id: c for c in credentials}

        providers_by_name = {p.name: p for p in providers if p.enabled}
        provider_creds: dict[int, list[Credential]] = {}
        for c in credentials:
            if c.enabled:
                provider_creds.setdefault(c.provider_id, []).append(c)

        resolved_aliases: dict[str, ResolvedAlias] = {}
        deps_by_alias: dict[int, list[Deployment]] = {}
        for d in deployments:
            deps_by_alias.setdefault(d.alias_id, []).append(d)

        for a in aliases:
            if not a.enabled:
                continue
            ra = ResolvedAlias(
                name=a.name,
                lb_strategy=a.lb_strategy,
                fallback_aliases=list(a.fallback_aliases or []),
                cache_enabled=a.cache_enabled,
            )
            for d in deps_by_alias.get(a.id, []):
                if not d.enabled:
                    continue
                cred = cred_by_id.get(d.credential_id)
                if cred is None or not cred.enabled:
                    continue
                prov = prov_by_id.get(cred.provider_id)
                if prov is None or not prov.enabled:
                    continue
                ra.deployments.append(
                    self._flatten(a.name, d, cred, prov)
                )
            resolved_aliases[a.name] = ra

        return Snapshot(
            aliases=resolved_aliases,
            providers_by_name=providers_by_name,
            provider_creds=provider_creds,
            loaded_at=time.monotonic(),
        )

    @staticmethod
    def _flatten(alias_name: str, d: Deployment, cred: Credential, prov: Provider) -> ResolvedDeployment:
        return ResolvedDeployment(
            deployment_id=d.id,
            alias_name=alias_name,
            provider_type=prov.provider_type,
            upstream_model=d.upstream_model,
            base_url=cred.base_url or prov.default_base_url,
            api_key=decrypt_secret(cred.api_key_enc),
            org=cred.org,
            extra_headers=dict(cred.extra_headers or {}),
            weight=d.weight,
            rpm_limit=d.rpm_limit,
            tpm_limit=d.tpm_limit,
            cred_rpm_limit=cred.rpm_limit,
            cred_tpm_limit=cred.tpm_limit,
            credential_id=cred.id,
            pinned_params=dict(d.pinned_params or {}),
            default_params=dict(d.default_params or {}),
            drop_params=list(d.drop_params or []),
            input_price=d.input_price,
            output_price=d.output_price,
        )


config_store = ConfigStore()
