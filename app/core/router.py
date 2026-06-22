from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import ResolvedAlias, ResolvedDeployment, Snapshot, config_store
from app.core.security import decrypt_secret


class RouteNotFound(Exception):
    pass


def _synthetic_alias_from_prefix(snapshot: Snapshot, provider_name: str, model: str) -> ResolvedAlias:
    """Handle `provider/model` form (e.g. openai/gpt-4o, kimi/moonshot-v1-8k):
    route by provider name, load-balance across that provider's credentials."""
    provider = snapshot.providers_by_name.get(provider_name)
    if provider is None:
        raise RouteNotFound(f"Unknown provider prefix: {provider_name!r}")

    creds = snapshot.provider_creds.get(provider.id, [])
    if not creds:
        raise RouteNotFound(f"Provider {provider_name!r} has no enabled credentials")

    alias_name = f"{provider_name}/{model}"
    # Inherit pricing from any deployment configured for this provider+model,
    # so prefix-routed calls are costed too (0.0 when none is configured).
    input_price, output_price = snapshot.model_prices.get((provider.id, model), (0.0, 0.0))
    ra = ResolvedAlias(name=alias_name, lb_strategy="round_robin", fallback_aliases=[], cache_enabled=None)
    for cred in creds:
        ra.deployments.append(
            ResolvedDeployment(
                deployment_id=-cred.id,  # negative => synthetic, no DB deployment row
                alias_name=alias_name,
                provider_name=provider.name,
                provider_type=provider.provider_type,
                upstream_model=model,
                base_url=cred.base_url or provider.default_base_url,
                api_key=decrypt_secret(cred.api_key_enc),
                org=cred.org,
                extra_headers=dict(cred.extra_headers or {}),
                weight=cred.weight,
                rpm_limit=None,
                tpm_limit=None,
                cred_rpm_limit=cred.rpm_limit,
                cred_tpm_limit=cred.tpm_limit,
                credential_id=cred.id,
                pinned_params={},
                default_params={},
                drop_params=[],
                input_price=input_price,
                output_price=output_price,
            )
        )
    return ra


async def resolve(session: AsyncSession, model: str) -> ResolvedAlias:
    """Resolve a client `model` string to an alias (load-balancing group).

    Resolution order:
      1. Exact configured alias name.
      2. `provider/model` prefix form -> synthesize an alias over the provider's
         credentials, transforming params via that provider's adapter.
    """
    snapshot = await config_store.get(session)

    alias = snapshot.aliases.get(model)
    if alias is not None and alias.deployments:
        return alias

    if "/" in model:
        provider_name, _, upstream_model = model.partition("/")
        return _synthetic_alias_from_prefix(snapshot, provider_name, upstream_model)

    if alias is not None:
        raise RouteNotFound(f"Alias {model!r} has no healthy deployments")
    raise RouteNotFound(f"No route for model {model!r}")


def new_request_id() -> str:
    return uuid.uuid4().hex
