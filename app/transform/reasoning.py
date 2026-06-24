"""Cross-provider reasoning / "thinking" mapping.

Clients speak TWO canonical OpenAI-style controls and the gateway translates
them into whatever each upstream actually expects:

    reasoning_effort   "minimal" | "low" | "medium" | "high" | "xhigh" | "max"
    thinking.type      "enabled" | "disabled"            (a simple on/off switch)

Either may be sent (or both). ``thinking.type: "disabled"`` forces reasoning
off; ``"enabled"`` turns it on at ``reasoning_effort`` (or ``medium`` if no
level was given). These map to each provider as:

    OpenAI / GPT-5 / o-series   reasoning_effort: "minimal" | "low" | "medium" | "high"
    Anthropic (Claude)          thinking: {type: "enabled", budget_tokens: N}
    Gemini (native adapter)     generationConfig.thinkingConfig: {thinkingLevel: ...}
    Gemini (OpenAI-compat)      reasoning_effort: "minimal".."high"; "none" disables (2.5)
    Qwen / 通义 (DashScope)      enable_thinking: bool + thinking_budget: N
    DeepSeek                    thinking: {type: "enabled"|"disabled"} + reasoning_effort: "high"|"max"
    Volcengine / 火山方舟 (豆包)   reasoning_effort (Seed 2.0); thinking: {type} respected (Seed 1.6)
    Moonshot / Kimi             thinking: {type: "enabled"|"disabled"}  (on/off only, no levels)
    Zhipu / GLM                 thinking: {type: "enabled"|"disabled"}  (on/off only, no levels)
    MiniMax (M3)                thinking: {type: "adaptive"|"disabled"}  (no levels; M2.x always on)
    OpenRouter (relay)          reasoning: {effort: ...} / {enabled: false}  (its own unified obj)

If the client already sent the *provider's own* native thinking parameter
(e.g. ``enable_thinking`` for Qwen, a ``thinking`` block for DeepSeek/Volc),
the gateway respects it and skips the canonical→native mapping.

The level value also accepts ``none`` (and synonyms ``off``/``false``) to
disable, or a bool. Anything unknown is treated as ``medium``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CANONICAL_FIELD = "reasoning_effort"

_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
_OFF = {"none", "off", "disabled", "false", "no", "0"}

# OpenAI's native reasoning_effort levels; higher canonical levels clamp to "high".
_OPENAI_LEVELS = ("minimal", "low", "medium", "high")

# Token budgets per level for providers that want an explicit budget.
_ANTHROPIC_BUDGET = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384,
                     "xhigh": 24576, "max": 32000}
# Gemini uses a `thinkingLevel` enum (minimal/low/medium/high); the higher
# canonical levels clamp to "high".
_GEMINI_LEVEL = {"minimal": "minimal", "low": "low", "medium": "medium",
                 "high": "high", "xhigh": "high", "max": "high"}
_QWEN_BUDGET = {"minimal": 1024, "low": 4096, "medium": 16384, "high": 32768,
                "xhigh": 36864, "max": 38912}


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


def _thinking_type(params: dict[str, Any]) -> str | None:
    """The client's ``thinking.type`` on/off switch, if present."""
    thinking = params.get("thinking")
    if isinstance(thinking, dict):
        ttype = thinking.get("type")
        if isinstance(ttype, str):
            return ttype.strip().lower()
    return None


def resolve_level(params: dict[str, Any]) -> str | None:
    """Canonical thinking level from ``reasoning_effort`` and/or ``thinking.type``.

    ``thinking.type: "disabled"`` forces ``none``; ``"enabled"`` turns reasoning
    on at the given level (``medium`` if none was supplied). Returns the level,
    ``none``, or ``None`` when the client specified nothing.
    """
    level = normalize_level(params[CANONICAL_FIELD]) if CANONICAL_FIELD in params else None
    ttype = _thinking_type(params)
    if ttype == "disabled":
        return "none"
    if ttype == "enabled":
        return level if (level and level != "none") else "medium"
    return level


# --- Dialect registry -------------------------------------------------------
#
# Each OpenAI-compatible vendor differs only in how the canonical thinking
# controls map to its native request fields. Instead of a central if/elif, every
# dialect registers a small handler plus the base_url substrings that identify
# it. Adding a provider = one @register_dialect function below; nothing else
# changes.


@dataclass
class _Dialect:
    name: str
    markers: tuple[str, ...]
    apply: Callable[[dict[str, Any]], None]


_DIALECTS: dict[str, _Dialect] = {}


def register_dialect(name: str, markers: tuple[str, ...] = ()) -> Callable[..., Any]:
    """Register a thinking-translation handler for an OpenAI-compatible dialect.

    ``markers`` are lowercase substrings matched against the deployment base_url;
    the first registered dialect with a matching marker wins. The dialect with no
    markers (``openai``) is the fallback.
    """
    def deco(fn: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
        _DIALECTS[name] = _Dialect(name, markers, fn)
        return fn
    return deco


def detect_openai_dialect(base_url: str | None, override: str | None = None) -> str:
    """Identify which OpenAI-compatible dialect a deployment speaks.

    An explicit ``override`` (the deployment's configured ``dialect``) always
    wins — this is the only reliable signal for aggregators (zenmux, openrouter,
    one-api, …) whose base_url points at the gateway, not the real backend, so
    marker matching can't tell a Kimi model from an OpenAI one. With no override,
    fall back to matching registered markers against the base_url; unknown
    endpoints default to ``openai``."""
    if override:
        name = override.strip().lower()
        if name in _DIALECTS:
            return name
    url = (base_url or "").lower()
    for d in _DIALECTS.values():
        if d.markers and any(m in url for m in d.markers):
            return d.name
    return "openai"


# Vendor tokens that identify a dialect from a `provider/model` routing string.
# Used only by prefix routing (`provider/model` form), which synthesizes a
# deployment on the fly and so has no stored `dialect` to carry. Conservative
# substring match against known vendor names; no match -> fall back to base_url
# detection. openai is intentionally absent: an unmatched string already falls
# back to the openai default, so listing it would be redundant.
_MODEL_DIALECT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anthropic", ("claude", "anthropic")),
    ("google", ("gemini", "google")),
    ("deepseek", ("deepseek",)),
    ("moonshot", ("moonshot", "kimi")),
    ("qwen", ("qwen", "tongyi", "qwq")),
    ("minimax", ("minimax", "abab")),
    ("glm", ("glm", "zhipu", "chatglm", "z-ai")),
    ("volc", ("doubao", "volcengine", "volc")),
)


def detect_dialect_from_model(text: str | None) -> str | None:
    """Best-effort dialect from a ``provider/model`` routing string.

    Prefix routing (e.g. ``zenmux/deepseek/deepseek-v4-flash``) has no stored
    deployment to carry an explicit ``dialect``, and an aggregator's base_url
    reveals nothing — but the model id usually names the real vendor. Match known
    vendor tokens; return ``None`` when none match so the caller falls back to
    base_url detection (which yields ``openai`` for unknown endpoints)."""
    if not text:
        return None
    s = text.lower()
    for name, aliases in _MODEL_DIALECT_ALIASES:
        if any(a in s for a in aliases):
            return name
    return None


def apply_openai_compat(body: dict[str, Any], base_url: str | None,
                        dialect: str | None = None) -> None:
    """Translate the canonical thinking controls in-place into the native fields
    of the deployment's dialect. ``dialect`` is the deployment's explicit
    override (use it for aggregators); without it the dialect is auto-detected
    from ``base_url``. If the client already supplied the provider's own native
    parameter it is respected (only foreign canonical fields the provider would
    reject are stripped)."""
    d = _DIALECTS.get(detect_openai_dialect(base_url, dialect))
    if d is not None:
        d.apply(body)


def _has_thinking_block(body: dict[str, Any]) -> bool:
    """A client-supplied ``thinking`` block is treated as the provider's native
    param (covers {type:...} toggles plus extras like Kimi's {keep:...})."""
    return isinstance(body.get("thinking"), dict)


@register_dialect("openai")
def _apply_openai(body: dict[str, Any]) -> None:
    # Native field is reasoning_effort; `thinking` is foreign and gets stripped.
    # OpenAI tops out at "high", so clamp xhigh/max down to it.
    level = resolve_level(body)
    body.pop("thinking", None)
    if level is None:
        return
    if level == "none":
        body.pop(CANONICAL_FIELD, None)
    else:
        body[CANONICAL_FIELD] = level if level in _OPENAI_LEVELS else "high"


@register_dialect("openrouter", ("openrouter",))
def _apply_openrouter(body: dict[str, Any]) -> None:
    # OpenRouter is a NORMALIZING relay: it does not take vendor-native thinking
    # blocks, only its own unified `reasoning` object ({effort|max_tokens|enabled}).
    # Sending both `reasoning` and `reasoning_effort` 400s some models, so emit
    # only `reasoning` and strip the rest. effort is low/medium/high (minimal ->
    # low, xhigh/max -> high). Respect a client-supplied `reasoning` object.
    if isinstance(body.get("reasoning"), dict):  # client gave the native param
        body.pop(CANONICAL_FIELD, None)
        body.pop("thinking", None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    body.pop("thinking", None)
    if level is None:
        return
    if level == "none":
        body["reasoning"] = {"enabled": False}
    else:
        effort = "low" if level == "minimal" else (
            level if level in ("low", "medium", "high") else "high")
        body["reasoning"] = {"effort": effort}


@register_dialect("qwen", ("dashscope", "aliyuncs"))
def _apply_qwen(body: dict[str, Any]) -> None:
    # Qwen native: enable_thinking (bool) + thinking_budget (tokens).
    if "enable_thinking" in body:  # client gave the native param → respect it
        body.pop(CANONICAL_FIELD, None)
        body.pop("thinking", None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    body.pop("thinking", None)
    if level is None:
        return
    if level == "none":
        body["enable_thinking"] = False
    else:
        body["enable_thinking"] = True
        body["thinking_budget"] = _QWEN_BUDGET[level]


@register_dialect("deepseek", ("deepseek",))
def _apply_deepseek(body: dict[str, Any]) -> None:
    # DeepSeek toggles thinking with `thinking.type` and accepts only "high"/"max"
    # for reasoning_effort. Its native param is the `thinking` block.
    if _has_thinking_block(body):
        enabled = body["thinking"].get("type") == "enabled"
        lv = normalize_level(body[CANONICAL_FIELD]) if CANONICAL_FIELD in body else None
        body.pop(CANONICAL_FIELD, None)
        if enabled and lv not in (None, "none"):
            body[CANONICAL_FIELD] = "max" if lv == "max" else "high"
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    if level is None:
        return
    if level == "none":
        body["thinking"] = {"type": "disabled"}
    else:
        body["thinking"] = {"type": "enabled"}
        body[CANONICAL_FIELD] = "max" if level == "max" else "high"


@register_dialect("volc", ("volces", "volcengine"))
def _apply_volc(body: dict[str, Any]) -> None:
    # Seed 2.0 takes reasoning_effort (minimal/low/medium/high; "minimal" == off).
    # Seed 1.6 uses a `thinking` toggle (enabled/disabled/auto) — respect a
    # client-supplied one. Never send both. No xhigh/max, so those clamp to high.
    if _has_thinking_block(body):
        body.pop(CANONICAL_FIELD, None)
        return
    level = resolve_level(body)
    body.pop("thinking", None)
    if level is None:
        return
    body[CANONICAL_FIELD] = "minimal" if level == "none" else (
        level if level in _OPENAI_LEVELS else "high")


@register_dialect("moonshot", ("moonshot",))
def _apply_moonshot(body: dict[str, Any]) -> None:
    # Moonshot/Kimi (k2.5/k2.6 …) toggles reasoning with a `thinking` block
    # ({type:...} plus extras like keep:"all") — no effort levels. Thinking-only
    # models force it on and reject "disabled" upstream. Respect a client block.
    if _has_thinking_block(body):
        body.pop(CANONICAL_FIELD, None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    if level is None:
        body.pop("thinking", None)
        return
    body["thinking"] = {"type": "disabled" if level == "none" else "enabled"}


@register_dialect("glm", ("bigmodel", "z.ai"))
def _apply_glm(body: dict[str, Any]) -> None:
    # Zhipu GLM (4.5/4.6/5.x): thinking {type: "enabled"|"disabled"} toggle;
    # thinking is enforced once enabled, no effort levels. Respect a client block.
    if _has_thinking_block(body):
        body.pop(CANONICAL_FIELD, None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    if level is None:
        body.pop("thinking", None)
        return
    body["thinking"] = {"type": "disabled" if level == "none" else "enabled"}


@register_dialect("minimax", ("minimax",))
def _apply_minimax(body: dict[str, Any]) -> None:
    # MiniMax-M3: thinking {type: "adaptive"|"disabled"}; on by default when
    # omitted. No effort levels and no "enabled" variant — an active level maps to
    # "adaptive" (model decides). M2.x ignores the toggle (thinking always on).
    # Respect a client-supplied thinking block.
    if _has_thinking_block(body):
        body.pop(CANONICAL_FIELD, None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    if level is None:
        body.pop("thinking", None)
        return
    body["thinking"] = {"type": "disabled" if level == "none" else "adaptive"}


@register_dialect("anthropic")
def _apply_anthropic(body: dict[str, Any]) -> None:
    # Claude via an OpenAI-compatible / aggregator endpoint takes the native
    # thinking block {type:"enabled", budget_tokens:N}. No markers — reached only
    # by an explicit dialect override (a direct Anthropic provider uses its own
    # adapter). A client block carrying budget_tokens is respected; disable =
    # omit the block.
    native = body.get("thinking")
    if isinstance(native, dict) and "budget_tokens" in native:
        body.pop(CANONICAL_FIELD, None)
        return
    level = resolve_level(body)
    body.pop(CANONICAL_FIELD, None)
    if level is None:
        return
    if level == "none":
        body.pop("thinking", None)
    else:
        body["thinking"] = {"type": "enabled", "budget_tokens": _ANTHROPIC_BUDGET[level]}


@register_dialect("google")
def _apply_google(body: dict[str, Any]) -> None:
    # Gemini via an OpenAI-compatible endpoint uses reasoning_effort
    # (minimal/low/medium/high); "none" disables thinking on 2.5 models (Pro/3
    # ignore it). Higher canonical levels clamp to "high". No markers — reached
    # only by an explicit dialect override (a direct Gemini provider uses its own
    # adapter). No thinking block.
    level = resolve_level(body)
    body.pop("thinking", None)
    if level is None:
        return
    body[CANONICAL_FIELD] = "none" if level == "none" else (
        level if level in _OPENAI_LEVELS else "high")


def anthropic_thinking(level: str | None) -> dict[str, Any] | None:
    """Return Anthropic's ``thinking`` block for a level, or None to leave it off."""
    if level is None or level == "none":
        return None
    return {"type": "enabled", "budget_tokens": _ANTHROPIC_BUDGET[level]}


def resolve_anthropic_thinking(params: dict[str, Any]) -> dict[str, Any] | None:
    """Anthropic ``thinking`` block for a request. A client-supplied native block
    (one carrying ``budget_tokens``) is respected as-is; otherwise it's derived
    from the canonical ``reasoning_effort`` / ``thinking.type`` controls."""
    native = params.get("thinking")
    if isinstance(native, dict) and "budget_tokens" in native:
        return native
    return anthropic_thinking(resolve_level(params))


def gemini_thinking_config(level: str | None) -> dict[str, Any] | None:
    """Return Gemini's ``thinkingConfig`` (``thinkingLevel`` enum) for a canonical
    level, or None to leave it unset. The higher canonical levels clamp to
    "high"; a disable request maps to the lowest level since Gemini has no hard
    "off"."""
    if level is None:
        return None
    lvl = "minimal" if level == "none" else _GEMINI_LEVEL[level]
    return {"thinkingLevel": lvl, "includeThoughts": False}
