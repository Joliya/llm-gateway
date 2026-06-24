from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import fx
from app.core.auth import require_admin
from app.db.models import Setting
from app.db.session import get_session
from app.schemas.admin import CurrencySetting

router = APIRouter(dependencies=[Depends(require_admin)])
_settings = get_settings()

# Built-in defaults, returned for any key the operator hasn't saved yet. New
# settings are added here (and given a validator below) — no schema change.
DEFAULTS: dict[str, Any] = {
    # Fresh installs ship with a common set of currencies (see fx.DEFAULT_RATES);
    # the auto-updater replaces the placeholder rates with live values.
    "currency": fx.DEFAULT_CURRENCY,
}

# Per-key validators: normalize/clamp an incoming value, or raise ValueError.
def _validate_currency(value: Any) -> dict[str, Any]:
    parsed = CurrencySetting.model_validate(value)
    rates = {code.upper(): rate for code, rate in parsed.rates.items()}
    for code, rate in rates.items():
        if rate <= 0:
            raise ValueError(f"rate for {code} must be positive")
    display = (parsed.display or "USD").upper()
    if display != "USD" and display not in rates:
        raise ValueError(f"display currency {display!r} is not in rates")
    return {"rates": rates, "display": display}


_VALIDATORS = {
    "currency": _validate_currency,
}


@router.get("")
async def list_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """All settings, with built-in defaults filled in for unsaved keys."""
    stored = {
        s.key: s.value
        for s in (await session.execute(select(Setting))).scalars().all()
    }
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


@router.post("/currency/reset-defaults")
async def reset_currency_defaults(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Replace the currency list with the seeded common set, then best-effort
    pull live rates. Lets an existing install (whose `currency` row predates the
    defaults) adopt the common currencies without manual entry."""
    value = _validate_currency(fx.DEFAULT_CURRENCY)
    row = await session.get(Setting, "currency")
    if row is None:
        session.add(Setting(key="currency", value=value))
    else:
        row.value = value
    await session.commit()
    # Best-effort: correct the placeholder rates with live values if reachable.
    try:
        await fx.refresh_rates(session, request.app.state.http_client, _settings.fx_fetch_timeout)
    except Exception:  # noqa: BLE001 — placeholders stand if the source is down
        pass
    row = await session.get(Setting, "currency")
    return {"currency": row.value if row is not None else value}


@router.post("/currency/refresh")
async def refresh_currency_rates(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Fetch fresh USD→other rates now (same path the daily job uses), for the
    currencies already configured. Returns whether anything was updated."""
    client = request.app.state.http_client
    updated = await fx.refresh_rates(session, client, _settings.fx_fetch_timeout)
    row = await session.get(Setting, "currency")
    value = row.value if row is not None else DEFAULTS["currency"]
    return {"updated": updated, "currency": value}


@router.put("/{key}")
async def put_setting(
    key: str, value: Any = Body(...), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if key not in DEFAULTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown setting {key!r}")
    validator = _VALIDATORS.get(key)
    if validator is not None:
        try:
            value = validator(value)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()
    return {key: value}
