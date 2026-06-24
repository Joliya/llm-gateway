from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import ProviderAdapter, UpstreamRequest, Usage
from app.transform.reasoning import apply_openai_compat

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatAdapter(ProviderAdapter):
    """Works with OpenAI and any OpenAI-compatible endpoint (Kimi/Moonshot,
    DeepSeek, 通义/DashScope-compatible, vLLM, etc.) — differs only by base_url.
    Request/response already match the OpenAI schema, so transforms are mostly
    pass-through."""

    provider_type = "openai_compat"

    def _headers(self, api_key: str, org: str | None, extra: dict[str, str]) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if org:
            headers["OpenAI-Organization"] = org
        headers.update(extra or {})
        return headers

    def build_chat_request(self, *, base_url, api_key, org, extra_headers, upstream_model,
                           params, dialect=None):
        body = dict(params)
        body["model"] = upstream_model
        apply_openai_compat(body, base_url, dialect)
        # Streaming responses omit token usage unless explicitly requested, which
        # would leave cost/usage logged as 0. Ask for the trailing usage chunk
        # (the executor already parses it) without clobbering a client's own opts.
        if body.get("stream"):
            opts = dict(body.get("stream_options") or {})
            opts.setdefault("include_usage", True)
            body["stream_options"] = opts
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        return UpstreamRequest(
            method="POST",
            url=f"{base}/chat/completions",
            headers=self._headers(api_key, org, extra_headers),
            json=body,
        )

    def build_embedding_request(self, *, base_url, api_key, org, extra_headers, upstream_model, params):
        body = dict(params)
        body["model"] = upstream_model
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        return UpstreamRequest(
            method="POST",
            url=f"{base}/embeddings",
            headers=self._headers(api_key, org, extra_headers),
            json=body,
        )

    def parse_chat_response(self, data: dict[str, Any]) -> dict[str, Any]:
        return data  # already OpenAI format

    def extract_usage(self, data: dict[str, Any]) -> Usage:
        u = data.get("usage") or {}
        return Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )

    async def transform_stream(self, lines: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
        async for raw in lines:
            line = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else raw.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue
