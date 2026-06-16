from __future__ import annotations

from app.providers.anthropic import AnthropicAdapter
from app.providers.base import ProviderAdapter
from app.providers.gemini import GeminiAdapter
from app.providers.openai_compat import OpenAICompatAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    "openai_compat": OpenAICompatAdapter(),
    "openai": OpenAICompatAdapter(),  # alias
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(provider_type: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[provider_type]
    except KeyError:
        raise ValueError(f"Unknown provider_type: {provider_type!r}")


def supported_provider_types() -> list[str]:
    return sorted(_ADAPTERS.keys())
