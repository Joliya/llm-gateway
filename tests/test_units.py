from __future__ import annotations

import pytest

from app.core.cost import compute_cost
from app.core.load_balancer import order_deployments
from app.core.rate_limiter import MemoryRateLimiter
from app.providers.base import Usage
from app.transform.params import apply_param_rules


def _dep(did: int, weight: int = 1):
    from app.core.config_store import ResolvedDeployment

    return ResolvedDeployment(
        deployment_id=did, alias_name="a", provider_type="openai_compat",
        upstream_model="m", base_url=None, api_key="k", org=None, extra_headers={},
        weight=weight, rpm_limit=None, tpm_limit=None, cred_rpm_limit=None,
        cred_tpm_limit=None, credential_id=did, pinned_params={}, default_params={},
        drop_params=[], input_price=0.0, output_price=0.0,
    )


def test_param_rules_order():
    client = {"model": "x", "temperature": 0.9, "top_p": 0.5, "logit_bias": {}}
    out = apply_param_rules(
        client,
        drop_params=["logit_bias"],
        default_params={"max_tokens": 256, "temperature": 0.1},
        pinned_params={"temperature": 0.0},
    )
    assert "model" not in out          # control field stripped
    assert "logit_bias" not in out     # dropped
    assert out["max_tokens"] == 256    # default filled (was absent)
    assert out["top_p"] == 0.5         # client value kept
    assert out["temperature"] == 0.0   # pinned overrides client + default


def test_cost_computation():
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000)
    # $1 / 1M input, $2 / 1M output
    assert compute_cost(usage, 1.0, 2.0) == pytest.approx(1.0 + 1.0)
    assert compute_cost(Usage(), 0.0, 0.0) == 0.0


async def test_rate_limiter_window():
    rl = MemoryRateLimiter()
    assert await rl.check("k", 2) is True
    assert await rl.check("k", 2) is True
    assert await rl.check("k", 2) is False   # third in same window blocked
    assert await rl.check("k", None) is True  # unlimited


def test_round_robin_rotates():
    deps = [_dep(1), _dep(2), _dep(3)]
    first = order_deployments("alias-rr", deps, "round_robin")[0].deployment_id
    second = order_deployments("alias-rr", deps, "round_robin")[0].deployment_id
    assert first != second  # cursor advanced


def test_circuit_broken_moved_to_back():
    from app.core.circuit_breaker import circuit_breaker

    deps = [_dep(10), _dep(11)]
    for _ in range(circuit_breaker._threshold):
        circuit_breaker.record_failure(10)
    ordered = order_deployments("alias-cb", deps, "round_robin")
    assert ordered[-1].deployment_id == 10  # broken one is last
    circuit_breaker.record_success(10)
