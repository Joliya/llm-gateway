from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UpstreamRequest:
    """Everything the executor needs to make one upstream HTTP call."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ProviderAdapter:
    """Translate between the OpenAI-compatible surface and a vendor API.

    Subclasses implement request/response transforms. The executor owns the
    actual HTTP call so that retry / fallback / circuit-breaking stay central.
    """

    provider_type: str = "base"

    # --- request building ---
    def build_chat_request(
        self,
        *,
        base_url: str,
        api_key: str,
        org: str | None,
        extra_headers: dict[str, str],
        upstream_model: str,
        params: dict[str, Any],
    ) -> UpstreamRequest:
        raise NotImplementedError

    def build_embedding_request(
        self,
        *,
        base_url: str,
        api_key: str,
        org: str | None,
        extra_headers: dict[str, str],
        upstream_model: str,
        params: dict[str, Any],
    ) -> UpstreamRequest:
        raise NotImplementedError

    # --- response parsing (non-stream) ---
    def parse_chat_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return an OpenAI-format chat.completion object."""
        raise NotImplementedError

    def extract_usage(self, data: dict[str, Any]) -> Usage:
        raise NotImplementedError

    # --- streaming ---
    async def transform_stream(
        self, lines: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield OpenAI-format chat.completion.chunk dicts from raw SSE lines."""
        raise NotImplementedError
