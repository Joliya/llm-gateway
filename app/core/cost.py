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
