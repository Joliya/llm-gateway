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
    assert detect_openai_dialect("https://ark.cn-beijing.volces.com/api/v3") == "volc"
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


def test_volc_maps_to_reasoning_effort():
    base = "https://ark.cn-beijing.volces.com/api/v3"
    # Seed 2.0 style: canonical level -> reasoning_effort (no thinking block)
    on = _chat(OpenAICompatAdapter(), base, {"messages": [], "reasoning_effort": "low"})
    assert on["reasoning_effort"] == "low"
    assert "thinking" not in on
    mx = _chat(OpenAICompatAdapter(), base, {"messages": [], "reasoning_effort": "max"})
    assert mx["reasoning_effort"] == "high"        # clamps to high (no xhigh/max)
    off = _chat(OpenAICompatAdapter(), base, {"messages": [], "reasoning_effort": "none"})
    assert off["reasoning_effort"] == "minimal"    # minimal == no thinking on Volc
    # Seed 1.6 style: explicit thinking toggle (incl. auto) respected, effort dropped
    tog = _chat(OpenAICompatAdapter(), base, {"messages": [], "thinking": {"type": "auto"},
                                              "reasoning_effort": "high"})
    assert tog["thinking"] == {"type": "auto"}
    assert "reasoning_effort" not in tog


def test_kimi_maps_to_thinking_toggle():
    # Kimi has no effort levels: any active level -> thinking enabled, effort dropped
    on = _chat(OpenAICompatAdapter(), "https://api.moonshot.cn/v1",
               {"messages": [], "reasoning_effort": "high"})
    assert on["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in on
    off = _chat(OpenAICompatAdapter(), "https://api.moonshot.cn/v1",
                {"messages": [], "reasoning_effort": "none"})
    assert off["thinking"] == {"type": "disabled"}
    # nothing specified -> leave it to Kimi's default (thinking on); no field added
    bare = _chat(OpenAICompatAdapter(), "https://api.moonshot.cn/v1", {"messages": []})
    assert "thinking" not in bare
    # native block with extras (preserved thinking) respected verbatim
    native = _chat(OpenAICompatAdapter(), "https://api.moonshot.cn/v1",
                   {"messages": [], "thinking": {"type": "enabled", "keep": "all"}})
    assert native["thinking"] == {"type": "enabled", "keep": "all"}


def test_openai_clamps_max_to_high():
    body = _chat(OpenAICompatAdapter(), "https://api.openai.com/v1",
                 {"messages": [], "reasoning_effort": "max"})
    assert body["reasoning_effort"] == "high"
    xh = _chat(OpenAICompatAdapter(), "https://api.openai.com/v1",
               {"messages": [], "reasoning_effort": "xhigh"})
    assert xh["reasoning_effort"] == "high"


def test_xhigh_is_a_valid_level():
    assert normalize_level("xhigh") == "xhigh"
    # xhigh gets a budget on budget-based providers (Qwen here)
    body = _chat(OpenAICompatAdapter(), "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 {"messages": [], "reasoning_effort": "xhigh"})
    assert body["enable_thinking"] is True
    assert body["thinking_budget"] > 0


def test_thinking_type_disabled_turns_off_everywhere():
    cases = {
        "https://api.openai.com/v1": lambda b: "reasoning_effort" not in b,
        "https://dashscope.aliyuncs.com/compatible-mode/v1": lambda b: b["enable_thinking"] is False,
        "https://api.deepseek.com/v1": lambda b: b["thinking"] == {"type": "disabled"},
        "https://ark.cn-beijing.volces.com/api/v3": lambda b: b["thinking"] == {"type": "disabled"},
        "https://api.moonshot.cn/v1": lambda b: b["thinking"] == {"type": "disabled"},
    }
    for base, check in cases.items():
        body = _chat(OpenAICompatAdapter(), base,
                     {"messages": [], "thinking": {"type": "disabled"}})
        assert check(body), base


def test_thinking_type_enabled_defaults_to_medium():
    # Qwen: enabled with no level -> medium budget
    body = _chat(OpenAICompatAdapter(), "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 {"messages": [], "thinking": {"type": "enabled"}})
    assert body["enable_thinking"] is True
    assert body["thinking_budget"] == 16384  # _QWEN_BUDGET["medium"]
    # OpenAI: enabled with no level -> medium
    oai = _chat(OpenAICompatAdapter(), "https://api.openai.com/v1",
                {"messages": [], "thinking": {"type": "enabled"}})
    assert oai["reasoning_effort"] == "medium"


def test_native_param_respected_over_canonical():
    # Qwen native enable_thinking is kept; canonical reasoning_effort stripped
    qwen = _chat(OpenAICompatAdapter(), "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 {"messages": [], "enable_thinking": False, "reasoning_effort": "high"})
    assert qwen["enable_thinking"] is False
    assert "reasoning_effort" not in qwen
    # DeepSeek native thinking block is kept; reasoning_effort clamped to high
    ds = _chat(OpenAICompatAdapter(), "https://api.deepseek.com/v1",
               {"messages": [], "thinking": {"type": "enabled"}, "reasoning_effort": "low"})
    assert ds["thinking"] == {"type": "enabled"}
    assert ds["reasoning_effort"] == "high"
    # Volc keeps the native toggle, drops the foreign reasoning_effort
    volc = _chat(OpenAICompatAdapter(), "https://ark.cn-beijing.volces.com/api/v3",
                 {"messages": [], "thinking": {"type": "enabled"}, "reasoning_effort": "low"})
    assert volc["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in volc


def test_anthropic_thinking_type_toggle_and_native_block():
    # thinking.type disabled -> no block
    off = _chat(AnthropicAdapter(), None, {"messages": [{"role": "user", "content": "hi"}],
                                           "thinking": {"type": "disabled"}})
    assert "thinking" not in off
    # native block (with budget_tokens) respected verbatim
    native = _chat(AnthropicAdapter(), None, {"messages": [{"role": "user", "content": "hi"}],
                                              "thinking": {"type": "enabled", "budget_tokens": 5000}})
    assert native["thinking"] == {"type": "enabled", "budget_tokens": 5000}


def test_gemini_thinking_type_disabled():
    body = _chat(GeminiAdapter(), None, {"messages": [{"role": "user", "content": "hi"}],
                                         "thinking": {"type": "disabled"}})
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"


def test_gemini_thinking_level_clamps_and_never_uses_budget():
    cfg = _chat(GeminiAdapter(), None,
                {"messages": [], "reasoning_effort": "high"})["generationConfig"]["thinkingConfig"]
    assert cfg["thinkingLevel"] == "high"
    assert "thinkingBudget" not in cfg                      # always level, never budget
    # xhigh/max clamp to high (thinkingLevel only has minimal/low/medium/high)
    xh = _chat(GeminiAdapter(), None,
               {"messages": [], "reasoning_effort": "xhigh"})["generationConfig"]["thinkingConfig"]
    assert xh["thinkingLevel"] == "high"


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
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "medium"
    off = _chat(GeminiAdapter(), None,
                {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "none"})
    assert off["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"


# --- multimodal image handling ---
from app.transform.multimodal import (  # noqa: E402
    ImageFetchError,
    has_remote_images,
    inline_remote_images,
    normalize_images,
    openai_content_to_anthropic,
    openai_content_to_gemini_parts,
    parse_data_uri,
)

_IMG_MSG = [{"role": "user", "content": [
    {"type": "text", "text": "what is this"},
    {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
]}]
_DATA_URI = "data:image/png;base64,aGVsbG8="


class _FakeResp:
    def __init__(self, content, ctype="image/png"):
        self.content = content
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        pass


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient.get used by image inlining."""

    def __init__(self, content=b"hello", ctype="image/png"):
        self.calls = 0
        self._content = content
        self._ctype = ctype

    async def get(self, url, **kw):
        self.calls += 1
        return _FakeResp(self._content, self._ctype)


def test_parse_data_uri():
    assert parse_data_uri(_DATA_URI) == ("image/png", "aGVsbG8=")
    assert parse_data_uri("https://x/y.png") is None
    assert parse_data_uri("data:image/png;base64,") is None


def test_has_remote_images():
    assert has_remote_images(_IMG_MSG) is True
    assert has_remote_images([{"role": "user", "content": "plain text"}]) is False
    data_only = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": _DATA_URI}}]}]
    assert has_remote_images(data_only) is False


@pytest.mark.asyncio
async def test_inline_remote_images_downloads_once():
    client = _FakeClient(content=b"hello")
    out = await inline_remote_images(client, _IMG_MSG, max_bytes=1024, timeout=5)
    url = out[0]["content"][1]["image_url"]["url"]
    assert url == "data:image/png;base64,aGVsbG8="  # base64("hello")
    # original message left untouched (new structures returned)
    assert _IMG_MSG[0]["content"][1]["image_url"]["url"].startswith("https://")
    # cache reuse: a second remote ref to the same url doesn't re-download
    cache: dict[str, str] = {}
    msgs = _IMG_MSG + [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]}]
    client2 = _FakeClient()
    await inline_remote_images(client2, msgs, max_bytes=1024, timeout=5, cache=cache)
    assert client2.calls == 1


@pytest.mark.asyncio
async def test_inline_rejects_oversized_image():
    client = _FakeClient(content=b"x" * 100)
    with pytest.raises(ImageFetchError):
        await inline_remote_images(client, _IMG_MSG, max_bytes=10, timeout=5)


class _Dep:
    def __init__(self, provider_type, base_url):
        self.provider_type = provider_type
        self.base_url = base_url


@pytest.mark.asyncio
async def test_normalize_images_inlines_for_kimi_and_gemini():
    for dep in (_Dep("openai_compat", "https://api.moonshot.cn/v1"),
                _Dep("gemini", None)):
        params = {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]}]}
        await normalize_images(_FakeClient(), dep, params)
        assert params["messages"][0]["content"][0]["image_url"]["url"].startswith("data:")


@pytest.mark.asyncio
async def test_normalize_images_leaves_url_providers_untouched():
    for dep in (_Dep("openai_compat", "https://api.openai.com/v1"),
                _Dep("openai_compat", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                _Dep("anthropic", None)):
        params = {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]}]}
        await normalize_images(_FakeClient(), dep, params)
        assert params["messages"][0]["content"][0]["image_url"]["url"].startswith("https://")


def test_anthropic_content_carries_image():
    blocks = openai_content_to_anthropic([
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": _DATA_URI}},
        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
    ])
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}
    assert blocks[2]["source"] == {"type": "url", "url": "https://x/y.png"}
    assert openai_content_to_anthropic("plain") == "plain"


def test_gemini_parts_carry_inline_image():
    parts = openai_content_to_gemini_parts([
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": _DATA_URI}},
        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},  # skipped: not inlined
    ])
    assert parts[0] == {"text": "hi"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}}
    assert len(parts) == 2  # remote url dropped


def test_anthropic_adapter_passes_image_through():
    body = _chat(AnthropicAdapter(), None, {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": _DATA_URI}}]}]})
    content = body["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in content)


def test_gemini_adapter_passes_image_through():
    body = _chat(GeminiAdapter(), None, {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": _DATA_URI}}]}]})
    parts = body["contents"][0]["parts"]
    assert parts[0]["inlineData"]["data"] == "aGVsbG8="
