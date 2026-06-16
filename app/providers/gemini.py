from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import ProviderAdapter, UpstreamRequest, Usage
from app.transform.reasoning import gemini_thinking_config, peek_effort

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
}

_GEN_CONFIG_MAP = {
    "max_tokens": "maxOutputTokens",
    "temperature": "temperature",
    "top_p": "topP",
    "top_k": "topK",
}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def _candidate_text(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = cands[0].get("content", {}).get("parts", []) or []
    # Skip thought parts so reasoning never leaks into the answer content.
    return "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")
    )


class GeminiAdapter(ProviderAdapter):
    provider_type = "gemini"

    def _to_gemini_body(self, params: dict[str, Any]) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for msg in params.get("messages", []):
            role = msg.get("role")
            text = _content_to_text(msg.get("content"))
            if role == "system":
                system_parts.append(text)
            else:
                contents.append(
                    {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
                )
        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        gen_config: dict[str, Any] = {}
        for src, dst in _GEN_CONFIG_MAP.items():
            if params.get(src) is not None:
                gen_config[dst] = params[src]
        if params.get("stop") is not None:
            stop = params["stop"]
            gen_config["stopSequences"] = [stop] if isinstance(stop, str) else stop

        thinking_config = gemini_thinking_config(peek_effort(params))
        if thinking_config is not None:
            gen_config["thinkingConfig"] = thinking_config

        if gen_config:
            body["generationConfig"] = gen_config
        return body

    def build_chat_request(self, *, base_url, api_key, org, extra_headers, upstream_model, params):
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        stream = bool(params.get("stream"))
        verb = "streamGenerateContent" if stream else "generateContent"
        suffix = "&alt=sse" if stream else ""
        url = f"{base}/v1beta/models/{upstream_model}:{verb}?key={api_key}{suffix}"
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        return UpstreamRequest(method="POST", url=url, headers=headers, json=self._to_gemini_body(params))

    def parse_chat_response(self, data: dict[str, Any]) -> dict[str, Any]:
        usage = self.extract_usage(data)
        cands = data.get("candidates") or [{}]
        finish = _FINISH_MAP.get(cands[0].get("finishReason"), "stop")
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("modelVersion", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _candidate_text(data)},
                    "finish_reason": finish,
                }
            ],
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
        }

    def extract_usage(self, data: dict[str, Any]) -> Usage:
        u = data.get("usageMetadata") or {}
        return Usage(
            prompt_tokens=u.get("promptTokenCount", 0),
            completion_tokens=u.get("candidatesTokenCount", 0),
            total_tokens=u.get("totalTokenCount", 0),
        )

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
            text = _candidate_text(event)
            cands = event.get("candidates") or [{}]
            finish = cands[0].get("finishReason")
            if text:
                yield {**base_chunk, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            if finish:
                yield {**base_chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": _FINISH_MAP.get(finish, "stop")}]}
