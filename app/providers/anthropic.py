from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import ProviderAdapter, UpstreamRequest, Usage
from app.transform.reasoning import anthropic_thinking, peek_effort

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096

_FINISH_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return "" if content is None else str(content)


class AnthropicAdapter(ProviderAdapter):
    provider_type = "anthropic"

    def _headers(self, api_key: str, extra: dict[str, str]) -> dict[str, str]:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        headers.update(extra or {})
        return headers

    def _to_anthropic_body(self, upstream_model: str, params: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": upstream_model}

        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for msg in params.get("messages", []):
            role = msg.get("role")
            if role == "system":
                system_parts.append(_content_to_text(msg.get("content")))
            else:
                messages.append(
                    {"role": role, "content": _content_to_text(msg.get("content"))}
                )
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        body["messages"] = messages

        body["max_tokens"] = params.get("max_tokens") or DEFAULT_MAX_TOKENS
        for src in ("temperature", "top_p", "top_k", "stream"):
            if src in params and params[src] is not None:
                body[src] = params[src]
        if params.get("stop") is not None:
            stop = params["stop"]
            body["stop_sequences"] = [stop] if isinstance(stop, str) else stop

        thinking = anthropic_thinking(peek_effort(params))
        if thinking is not None:
            body["thinking"] = thinking
            # Extended thinking requires max_tokens to exceed the thinking budget,
            # and forbids sampling overrides (temperature must be the default 1).
            needed = thinking["budget_tokens"] + DEFAULT_MAX_TOKENS
            if body["max_tokens"] < needed:
                body["max_tokens"] = needed
            for unsupported in ("temperature", "top_p", "top_k"):
                body.pop(unsupported, None)
        return body

    def build_chat_request(self, *, base_url, api_key, org, extra_headers, upstream_model, params):
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        return UpstreamRequest(
            method="POST",
            url=f"{base}/v1/messages",
            headers=self._headers(api_key, extra_headers),
            json=self._to_anthropic_body(upstream_model, params),
        )

    def parse_chat_response(self, data: dict[str, Any]) -> dict[str, Any]:
        text = _content_to_text(data.get("content"))
        usage = self.extract_usage(data)
        return {
            "id": data.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": _FINISH_MAP.get(data.get("stop_reason"), "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
        }

    def extract_usage(self, data: dict[str, Any]) -> Usage:
        u = data.get("usage") or {}
        pin = u.get("input_tokens", 0)
        pout = u.get("output_tokens", 0)
        return Usage(prompt_tokens=pin, completion_tokens=pout, total_tokens=pin + pout)

    async def transform_stream(self, lines: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
        cid = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        base_chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": ""}
        async for raw in lines:
            line = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else raw.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    choice = {"index": 0, "delta": {"content": delta.get("text", "")},
                              "finish_reason": None}
                    yield {**base_chunk, "choices": [choice]}
            elif etype == "message_delta":
                stop = event.get("delta", {}).get("stop_reason")
                if stop:
                    choice = {"index": 0, "delta": {},
                              "finish_reason": _FINISH_MAP.get(stop, "stop")}
                    yield {**base_chunk, "choices": [choice]}
            elif etype == "message_stop":
                break
