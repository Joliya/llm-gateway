from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VirtualKey


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _period_elapsed(anchor: dt.datetime, period: str, now: dt.datetime) -> bool:
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=dt.timezone.utc)
    if period == "daily":
        return now.date() > anchor.date()
    if period == "monthly":
        return (now.year, now.month) > (anchor.year, anchor.month)
    return False  # "total" never resets


async def reset_if_needed(session: AsyncSession, vk: VirtualKey) -> None:
    """Roll over spend at the start of a new daily/monthly window."""
    now = _now()
    if _period_elapsed(vk.budget_anchor, vk.budget_period, now):
        vk.spend = 0.0
        vk.budget_anchor = now
        await session.flush()


def is_over_budget(vk: VirtualKey) -> bool:
    if vk.max_budget is None:
        return False
    return vk.spend >= vk.max_budget


async def add_spend(session: AsyncSession, vk: VirtualKey, cost: float) -> None:
    if cost <= 0:
        return
    vk.spend = (vk.spend or 0.0) + cost
    await session.flush()
