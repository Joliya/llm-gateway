from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VirtualKey


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _period_elapsed(anchor: dt.datetime, period: str, now: dt.datetime) -> bool:
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=dt.UTC)
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


async def sweep_expired(session: AsyncSession) -> int:
    """Reset spend for every key whose daily/monthly window has rolled over.

    `reset_if_needed` only fires when a key is used; this sweep keeps idle keys'
    spend/anchor correct too (so reports and the console don't show stale spend).
    Returns the number of keys reset.
    """
    now = _now()
    keys = (
        await session.execute(
            select(VirtualKey).where(VirtualKey.budget_period.in_(("daily", "monthly")))
        )
    ).scalars().all()
    reset = 0
    for vk in keys:
        if _period_elapsed(vk.budget_anchor, vk.budget_period, now):
            vk.spend = 0.0
            vk.budget_anchor = now
            reset += 1
    if reset:
        await session.commit()
    return reset


def is_over_budget(vk: VirtualKey) -> bool:
    if vk.max_budget is None:
        return False
    return vk.spend >= vk.max_budget


async def add_spend(session: AsyncSession, vk: VirtualKey, cost: float) -> None:
    """Increment spend atomically in the database.

    Computing `spend + cost` in SQL (rather than read-modify-write on the ORM
    object) makes concurrent requests for the same key — across async tasks or
    across replicas — safe from lost updates. We deliberately do NOT also mutate
    `vk.spend` in memory: that would mark the instance dirty and have the ORM
    emit a second, absolute `UPDATE spend=<value>` on commit, clobbering the
    atomic increment. Nothing reads `vk.spend` again this request; the next
    request loads the key fresh.
    """
    if cost <= 0:
        return
    await session.execute(
        update(VirtualKey)
        .where(VirtualKey.id == vk.id)
        .values(spend=VirtualKey.spend + cost)
    )
