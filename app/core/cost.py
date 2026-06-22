from __future__ import annotations

from app.config import get_settings
from app.providers.base import Usage

_settings = get_settings()

# litellm's convention: the proxy returns the computed cost in this response
# header, and litellm-based clients forward every response header into
# response._hidden_params["additional_headers"]["llm_provider-<name>"], from
# which the SDK lifts response_cost. Emitting the same header makes our cost
# flow through litellm/DSPy into Opik without any client-side change.
COST_HEADER = "x-litellm-response-cost"


def compute_cost(usage: Usage, input_price: float, output_price: float) -> float:
    """Cost from per-1M-token prices. Returns 0.0 when prices are unset."""
    if not input_price and not output_price:
        return 0.0
    return (usage.prompt_tokens / 1_000_000) * input_price + (
        usage.completion_tokens / 1_000_000
    ) * output_price


def cost_headers(cost: float) -> dict[str, str]:
    """litellm-compatible cost header for a response, or {} when disabled."""
    if not _settings.cost_header_enabled:
        return {}
    return {COST_HEADER: str(float(cost))}


def usage_from_openai(data: dict) -> Usage:
    """Extract token usage from an OpenAI-style response, tolerating both the
    chat shape (prompt_tokens/completion_tokens) and the Responses/newer shape
    (input_tokens/output_tokens). Returns zeros when usage is absent (common for
    images / audio), which simply yields cost 0."""
    u = data.get("usage") or {}
    prompt = u.get("prompt_tokens", u.get("input_tokens", 0)) or 0
    completion = u.get("completion_tokens", u.get("output_tokens", 0)) or 0
    total = u.get("total_tokens", prompt + completion) or 0
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
