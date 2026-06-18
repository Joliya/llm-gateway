from __future__ import annotations

import itertools
import random
import threading
from collections import defaultdict

from app.core.circuit_breaker import circuit_breaker
from app.core.config_store import ResolvedDeployment

# Per-alias round-robin cursors and in-flight counters.
_rr_counters: dict[str, itertools.count] = defaultdict(lambda: itertools.count())
_inflight: dict[int, int] = defaultdict(int)
_lock = threading.Lock()


def incr_inflight(deployment_id: int) -> None:
    with _lock:
        _inflight[deployment_id] += 1


def decr_inflight(deployment_id: int) -> None:
    with _lock:
        _inflight[deployment_id] = max(0, _inflight[deployment_id] - 1)


def order_deployments(
    alias_name: str,
    deployments: list[ResolvedDeployment],
    strategy: str,
) -> list[ResolvedDeployment]:
    """Return deployments ordered by the chosen strategy, with circuit-broken
    ones moved to the back (still tried as a last resort rather than failing
    outright). The first element is the primary pick; the rest form the
    in-pool retry order."""
    available = [d for d in deployments if circuit_breaker.is_available(d.deployment_id)]
    broken = [d for d in deployments if not circuit_breaker.is_available(d.deployment_id)]

    ordered = _apply_strategy(alias_name, available, strategy)
    return ordered + broken


def _apply_strategy(
    alias_name: str, deployments: list[ResolvedDeployment], strategy: str
) -> list[ResolvedDeployment]:
    if not deployments:
        return []

    if strategy == "random":
        pool = deployments[:]
        random.shuffle(pool)
        return pool

    if strategy == "least_busy":
        return sorted(deployments, key=lambda d: _inflight.get(d.deployment_id, 0))

    if strategy == "weighted":
        # Weighted shuffle: expand by weight, dedupe preserving first occurrence.
        expanded: list[ResolvedDeployment] = []
        for d in deployments:
            expanded.extend([d] * max(1, d.weight))
        random.shuffle(expanded)
        seen: set[int] = set()
        out: list[ResolvedDeployment] = []
        for d in expanded:
            if d.deployment_id not in seen:
                seen.add(d.deployment_id)
                out.append(d)
        return out

    # default: round_robin — rotate the pool by a per-alias cursor.
    start = next(_rr_counters[alias_name]) % len(deployments)
    return deployments[start:] + deployments[:start]
