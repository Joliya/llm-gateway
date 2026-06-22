"""Cross-provider reasoning / "thinking" mapping.

Clients speak ONE canonical OpenAI-style field — ``reasoning_effort`` — and the
gateway translates it into whatever each upstream actually expects:

    OpenAI / GPT-5 / o-series   reasoning_effort: "minimal" | "low" | "medium" | "high"
    Anthropic (Claude)          thinking: {type: "enabled", budget_tokens: N}
    Gemini (2.5)                generationConfig.thinkingConfig: {thinkingBudget: N}
    Qwen / 通义 (DashScope)      enable_thinking: bool + thinking_budget: N
    DeepSeek                    thinking: {type: "enabled"|"disabled"} + reasoning_effort: "high"|"max"
    Volcengine / 火山方舟 (豆包)   thinking: {type: "enabled"|"disabled"}  (on/off only, no levels)
    Kimi / Moonshot             (model-based; no param — field is dropped)

Canonical value accepts the level strings ``minimal|low|medium|high|max``,
``none`` (and synonyms ``off``/``false``) to disable, or a bool. Anything
unknown is treated as ``medium``.
"""

from typing import Any

CANONICAL_FIELD = "reasoning_effort"

_LEVELS = ("minimal", "low", "medium", "high", "max")
_OFF = {"none", "off", "disabled", "false", "no", "0"}

# Token budgets per level for providers that want an explicit budget.
_ANTHROPIC_BUDGET = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384, "max": 32000}
_GEMINI_BUDGET = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576, "max": 32768}
_QWEN_BUDGET = {"minimal": 1024, "low": 4096, "medium": 16384, "high": 32768, "max": 38912}


def normalize_level(value: Any) -> str | None:
    """Coerce a client value to ``none`` | ``minimal`` | ``low`` | ``medium`` | ``high``.

    Returns ``None`` only when there is nothing to interpret (value is ``None``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "medium" if value else "none"
    if isinstance(value, (int, float)):
        return "none" if value <= 0 else "medium"
    s = str(value).strip().lower()
    if not s:
        return None
    if s in _OFF:
        return "none"
    if s in _LEVELS:
        return s
    if s == "default":
        return "medium"
    return "medium"


def pop_effort(params: dict[str, Any]) -> str | None:
    """Remove the canonical field from ``params`` and return its normalized level."""
    if CANONICAL_FIELD not in params:
        return None
    return normalize_level(params.pop(CANONICAL_FIELD))


def peek_effort(params: dict[str, Any]) -> str | None:
    """Read the canonical level without mutating ``params``."""
    if CANONICAL_FIELD not in params:
        return None
    return normalize_level(params.get(CANONICAL_FIELD))


def detect_openai_dialect(base_url: str | None) -> str:
    """Identify which OpenAI-compatible vendor a base_url points at.

    Returns one of: ``openai`` | ``qwen`` | ``deepseek`` | ``volc`` | ``kimi``.
    Unknown endpoints default to ``openai`` semantics (pass the field through).
    """
    url = (base_url or "").lower()
    if "dashscope" in url or "aliyuncs" in url:
        return "qwen"
    if "deepseek" in url:
        return "deepseek"
    if "volces" in url or "volcengine" in url:
        return "volc"
    if "moonshot" in url:
        return "kimi"
    return "openai"


def apply_openai_compat(body: dict[str, Any], base_url: str | None) -> None:
    """Translate the canonical field in-place for an OpenAI-compatible body."""
    level = pop_effort(body)
    if level is None:
        return
    dialect = detect_openai_dialect(base_url)

    if dialect == "qwen":
        if level == "none":
            body["enable_thinking"] = False
        else:
            body["enable_thinking"] = True
            body["thinking_budget"] = _QWEN_BUDGET[level]
        return

    if dialect == "deepseek":
        # DeepSeek (deepseek-v4-pro …) toggles thinking with `thinking.type` and
        # accepts only "high"/"max" for reasoning_effort. Thinking is on by
        # default upstream, so we must explicitly disable it for "none".
        if level == "none":
            body["thinking"] = {"type": "disabled"}
        else:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "max" if level == "max" else "high"
        return

    if dialect == "volc":
        # Volcengine Ark / 豆包 (Doubao) toggles reasoning with `thinking.type`
        # (enabled/disabled) only — no effort levels or budget. Map any active
        # level to enabled, "none" to disabled.
        body["thinking"] = {"type": "disabled" if level == "none" else "enabled"}
        return

    if dialect == "kimi":
        # Reasoning is selected by choosing a thinking model variant, not a
        # request param — the canonical field is simply dropped (already popped).
        return

    # openai (and unknown compat endpoints): keep OpenAI's native field.
    # OpenAI has no "max"; clamp it down to its top supported level.
    if level != "none":
        body["reasoning_effort"] = "high" if level == "max" else level


def anthropic_thinking(level: str | None) -> dict[str, Any] | None:
    """Return Anthropic's ``thinking`` block for a level, or None to leave it off."""
    if level is None or level == "none":
        return None
    return {"type": "enabled", "budget_tokens": _ANTHROPIC_BUDGET[level]}


def gemini_thinking_config(level: str | None) -> dict[str, Any] | None:
    """Return Gemini's ``thinkingConfig`` for a level, or None to leave it unset."""
    if level is None:
        return None
    if level == "none":
        return {"thinkingBudget": 0}
    return {"thinkingBudget": _GEMINI_BUDGET[level], "includeThoughts": False}
