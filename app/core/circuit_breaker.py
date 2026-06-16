from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import get_settings

_settings = get_settings()


@dataclass
class _State:
    failures: int = 0
    open_until: float = 0.0  # monotonic timestamp; >now means cooling down


class CircuitBreaker:
    """Per-deployment failure tracking with cooldown.

    After `threshold` consecutive failures the deployment is "open" (skipped by
    the load balancer) for `cooldown` seconds, then half-open: the next attempt
    is allowed and either closes the circuit (success) or re-opens it (failure).
    """

    def __init__(self, threshold: int | None = None, cooldown: float | None = None) -> None:
        self._threshold = threshold or _settings.cb_failure_threshold
        self._cooldown = cooldown or _settings.cb_cooldown_seconds
        self._states: dict[int, _State] = {}

    def is_available(self, deployment_id: int) -> bool:
        st = self._states.get(deployment_id)
        if st is None:
            return True
        return time.monotonic() >= st.open_until

    def record_success(self, deployment_id: int) -> None:
        self._states[deployment_id] = _State()

    def record_failure(self, deployment_id: int) -> None:
        st = self._states.setdefault(deployment_id, _State())
        st.failures += 1
        if st.failures >= self._threshold:
            st.open_until = time.monotonic() + self._cooldown
            st.failures = 0  # reset; half-open after cooldown

    def status(self) -> dict[int, dict]:
        now = time.monotonic()
        return {
            did: {
                "available": now >= st.open_until,
                "failures": st.failures,
                "cooldown_remaining": max(0.0, round(st.open_until - now, 1)),
            }
            for did, st in self._states.items()
        }


circuit_breaker = CircuitBreaker()
