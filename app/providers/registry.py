from __future__ import annotations

from app.providers.anthropic import AnthropicAdapter
from app.providers.base import ProviderAdapter
from app.providers.gemini import GeminiAdapter
from app.providers.openai_compat import OpenAICompatAdapter
from app.providers.vertex import VertexAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    "openai_compat": OpenAICompatAdapter(),
    "openai": OpenAICompatAdapter(),  # alias
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),         # Gemini via AI Studio (API key)
    "vertex": VertexAdapter(),         # Gemini via Vertex AI (OAuth or express)
}


def get_adapter(provider_type: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[provider_type]
    except KeyError as exc:
        raise ValueError(f"Unknown provider_type: {provider_type!r}") from exc


def supported_provider_types() -> list[str]:
    return sorted(_ADAPTERS.keys())
