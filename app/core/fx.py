"""Daily refresh of USD→other-currency exchange rates for the console's
`currency` setting.

Rates come from free, no-key public APIs tried in order — exchangerate-api's
open endpoint first, then fallbacks — so a single source being down or
rate-limited doesn't stop the refresh. We only update currencies the operator
has already configured; we never add new ones or change the display currency.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

_log = logging.getLogger("llm_gateway.fx")

# Common currencies seeded into a brand-new install so the console ships with a
# useful list instead of an empty one. The rates here are rough placeholders —
# the startup/daily refresh (and the "Refresh now" button) replace them with
# live values. Operators can add or remove currencies freely afterward; once a
# `currency` setting exists we never re-seed, so clearing the list sticks.
DEFAULT_RATES: dict[str, float] = {
    "CNY": 7.2, "EUR": 0.92, "GBP": 0.79, "JPY": 150.0, "HKD": 7.8,
    "KRW": 1350.0, "SGD": 1.35, "AUD": 1.52, "CAD": 1.36,
}
DEFAULT_CURRENCY: dict[str, Any] = {"rates": dict(DEFAULT_RATES), "display": "USD"}


# Each source yields USD-based rates as {CODE: rate}. Listed in priority order;
# the first that returns a usable map wins. All use USD as the base currency.
def _parse_er_api(data: Any) -> dict[str, float] | None:
    # https://open.er-api.com/v6/latest/USD  (exchangerate-api, open/no-key)
    if isinstance(data, dict) and data.get("result") == "success":
        rates = data.get("rates")
        if isinstance(rates, dict):
            return rates
    return None


def _parse_rates_field(data: Any) -> dict[str, float] | None:
    # Frankfurter (ECB data) and exchangerate.host share a {"rates": {...}} shape.
    if isinstance(data, dict) and data.get("success") is not False:
        rates = data.get("rates")
        if isinstance(rates, dict) and rates:
            return rates
    return None


SOURCES: list[tuple[str, str, Callable[[Any], dict[str, float] | None]]] = [
    ("exchangerate-api", "https://open.er-api.com/v6/latest/USD", _parse_er_api),
    ("frankfurter", "https://api.frankfurter.app/latest?base=USD", _parse_rates_field),
    ("exchangerate.host", "https://api.exchangerate.host/latest?base=USD", _parse_rates_field),
]


async def fetch_usd_rates(
    client: httpx.AsyncClient, timeout: float
) -> tuple[str, dict[str, float]] | None:
    """Return (source_name, {CODE: rate}) from the first source that responds
    with a usable map, or None if every source fails."""
    for name, url, parse in SOURCES:
        try:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            rates = parse(resp.json())
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("fx source %s failed: %s", name, exc)
            continue
        if rates:
            # Normalize keys to upper-case ISO codes and coerce to float.
            clean: dict[str, float] = {}
            for code, rate in rates.items():
                try:
                    clean[str(code).upper()] = float(rate)
                except (TypeError, ValueError):
                    continue
            if clean:
                return name, clean
    return None


async def refresh_rates(
    session: AsyncSession,
    client: httpx.AsyncClient,
    timeout: float,
    throttle: float | None = None,
) -> bool:
    """Refresh the configured currencies' rates in place.

    `throttle`: if set and the setting was updated less than that many seconds
    ago, skip (used at startup so restarts don't hammer the public API or clobber
    a just-saved manual edit). Returns True if rates were written.
    """
    row = await session.get(Setting, "currency")
    if row is not None and isinstance(row.value, dict):
        # An existing setting is authoritative — refresh exactly what it tracks
        # (and if the operator cleared it to empty, leave it empty).
        value = row.value
    else:
        # Brand-new install: seed the common currencies so first boot lands real
        # rates for a useful default set.
        value = DEFAULT_CURRENCY
    tracked: dict[str, float] = dict(value.get("rates") or {})
    if not tracked:
        return False  # nothing configured to refresh

    if throttle and row is not None and row.updated_at is not None:
        updated = row.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=dt.UTC)
        age = (dt.datetime.now(dt.UTC) - updated).total_seconds()
        if age < throttle:
            return False

    fetched = await fetch_usd_rates(client, timeout)
    if fetched is None:
        _log.warning("fx refresh: all sources failed; keeping existing rates")
        return False
    source, usd_rates = fetched

    # Only touch currencies the operator already tracks; keep the old value for
    # any the source happens not to cover.
    updated_codes = []
    for code in tracked:
        if code in usd_rates:
            tracked[code] = usd_rates[code]
            updated_codes.append(code)
    if not updated_codes:
        _log.warning("fx refresh: source %s covered none of %s", source, list(tracked))
        return False

    new_value = {"rates": tracked, "display": value.get("display", "USD")}
    if row is None:
        session.add(Setting(key="currency", value=new_value))
    else:
        row.value = new_value
    await session.commit()
    _log.info("fx refresh: updated %s from %s", updated_codes, source)
    return True
