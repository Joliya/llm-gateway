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
        deployment_id=did, alias_name="a", provider_name="p", provider_type="openai_compat",
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


# --- cross-provider reasoning / thinking mapping ---

from app.providers.anthropic import AnthropicAdapter
from app.providers.gemini import GeminiAdapter
from app.providers.openai_compat import OpenAICompatAdapter
from app.transform.reasoning import (
    detect_openai_dialect,
    normalize_level,
)


def _chat(adapter, base_url, params):
    return adapter.build_chat_request(
        base_url=base_url, api_key="k", org=None, extra_headers={},
        upstream_model="m", params=params,
    ).json


def test_normalize_level():
    assert normalize_level("high") == "high"
    assert normalize_level("HIGH") == "high"
    assert normalize_level(True) == "medium"
    assert normalize_level(False) == "none"
    assert normalize_level("off") == "none"
    assert normalize_level("default") == "medium"
    assert normalize_level("weird") == "medium"
    assert normalize_level(None) is None


def test_dialect_detection():
    assert detect_openai_dialect("https://dashscope.aliyuncs.com/compatible-mode/v1") == "qwen"
    assert detect_openai_dialect("https://api.deepseek.com/v1") == "deepseek"
    assert detect_openai_dialect("https://api.moonshot.cn/v1") == "kimi"
    assert detect_openai_dialect("https://api.openai.com/v1") == "openai"
    assert detect_openai_dialect(None) == "openai"


def test_openai_keeps_reasoning_effort():
    body = _chat(OpenAICompatAdapter(), "https://api.openai.com/v1",
                 {"messages": [], "reasoning_effort": "high"})
    assert body["reasoning_effort"] == "high"


def test_qwen_maps_to_enable_thinking():
    body = _chat(OpenAICompatAdapter(), "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 {"messages": [], "reasoning_effort": "low"})
    assert "reasoning_effort" not in body
    assert body["enable_thinking"] is True
    assert body["thinking_budget"] > 0
    off = _chat(OpenAICompatAdapter(), "https://dashscope.aliyuncs.com/compatible-mode/v1",
                {"messages": [], "reasoning_effort": "none"})
    assert off["enable_thinking"] is False


def test_deepseek_maps_to_thinking_toggle():
    on = _chat(OpenAICompatAdapter(), "https://api.deepseek.com/v1",
               {"messages": [], "reasoning_effort": "low"})
    assert on["thinking"] == {"type": "enabled"}
    assert on["reasoning_effort"] == "high"            # DeepSeek floors to high
    mx = _chat(OpenAICompatAdapter(), "https://api.deepseek.com/v1",
               {"messages": [], "reasoning_effort": "max"})
    assert mx["reasoning_effort"] == "max"
    off = _chat(OpenAICompatAdapter(), "https://api.deepseek.com/v1",
                {"messages": [], "reasoning_effort": "none"})
    assert off["thinking"] == {"type": "disabled"}


def test_kimi_drops_field():
    body = _chat(OpenAICompatAdapter(), "https://api.moonshot.cn/v1",
                 {"messages": [], "reasoning_effort": "high"})
    assert "reasoning_effort" not in body
    assert "thinking" not in body


def test_openai_clamps_max_to_high():
    body = _chat(OpenAICompatAdapter(), "https://api.openai.com/v1",
                 {"messages": [], "reasoning_effort": "max"})
    assert body["reasoning_effort"] == "high"


def test_anthropic_maps_to_thinking_block():
    body = _chat(AnthropicAdapter(), None,
                 {"messages": [{"role": "user", "content": "hi"}],
                  "temperature": 0.7, "reasoning_effort": "high"})
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] > 0
    assert body["max_tokens"] > body["thinking"]["budget_tokens"]
    assert "temperature" not in body  # forbidden alongside thinking


def test_anthropic_no_thinking_when_off():
    body = _chat(AnthropicAdapter(), None,
                 {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.7})
    assert "thinking" not in body
    assert body["temperature"] == 0.7


def test_gemini_maps_to_thinking_config():
    body = _chat(GeminiAdapter(), None,
                 {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "medium"})
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] > 0
    off = _chat(GeminiAdapter(), None,
                {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "none"})
    assert off["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
