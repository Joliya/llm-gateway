from __future__ import annotations

from app.providers.base import Usage


def compute_cost(usage: Usage, input_price: float, output_price: float) -> float:
    """Cost from per-1M-token prices. Returns 0.0 when prices are unset."""
    if not input_price and not output_price:
        return 0.0
    return (usage.prompt_tokens / 1_000_000) * input_price + (
        usage.completion_tokens / 1_000_000
    ) * output_price
