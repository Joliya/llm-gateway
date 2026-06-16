from __future__ import annotations

import json

import httpx
import respx


def _openai_response(content: str = "hi", model: str = "gpt-x"):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@respx.mock
async def test_chat_pinned_param_and_response(app_client):
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_response())

    respx.post("https://up.test/v1/chat/completions").mock(side_effect=handler)

    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.9,  # should be overridden by pinned 0.0
    })
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "hi"
    assert captured["body"]["temperature"] == 0.0      # pinned won
    assert captured["body"]["model"] == "gpt-x"        # upstream model used


@respx.mock
async def test_fallback_on_429(app_client):
    route = respx.post("https://up.test/v1/chat/completions")
    # First deployment 429, retry hits second deployment OK.
    route.side_effect = [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json=_openai_response("recovered")),
    ]
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "recovered"
    assert route.call_count == 2


@respx.mock
async def test_prefix_routing(app_client):
    captured = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_response(model="moonshot-v1-8k"))

    respx.post("https://up.test/v1/chat/completions").mock(side_effect=handler)

    # provider/model form: "mockoai/moonshot-v1-8k"
    r = await app_client.post("/v1/chat/completions", json={
        "model": "mockoai/moonshot-v1-8k",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text
    assert captured["body"]["model"] == "moonshot-v1-8k"


@respx.mock
async def test_streaming(app_client):
    sse = (
        'data: {"choices":[{"index":0,"delta":{"content":"Hel"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "x"}], "stream": True,
    })
    assert r.status_code == 200
    body = r.text
    assert "Hel" in body and "lo" in body
    assert "data: [DONE]" in body


@respx.mock
async def test_models_listing(app_client):
    r = await app_client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "balanced" in ids


async def test_admin_create_key_returns_plaintext_once(app_client):
    M = {"Authorization": "Bearer test-master"}
    r = await app_client.post("/admin/keys", headers=M,
                              json={"name": "team-b", "allowed_aliases": ["balanced"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"].startswith("sk-gw-")       # plaintext returned once
    assert body["key_prefix"].startswith("sk-gw-")
    # listing must never expose the plaintext/hash
    lst = await app_client.get("/admin/keys", headers=M)
    assert all("key" not in row for row in lst.json())


@respx.mock
async def test_playground_chat_returns_routing_meta(app_client):
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response("pong"))
    )
    r = await app_client.post(
        "/admin/playground/chat",
        headers={"Authorization": "Bearer test-master"},
        json={"model": "balanced", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["content"] == "pong"
    assert data["meta"]["alias"] == "balanced"
    assert data["meta"]["total_tokens"] == 15
    assert data["meta"]["provider_type"] == "openai_compat"


async def test_playground_requires_master_key(app_client):
    # carries a virtual key, not the master key
    r = await app_client.post("/admin/playground/chat",
                              json={"model": "balanced", "messages": []})
    assert r.status_code == 401


async def test_admin_requires_master_key(app_client):
    # app_client carries a virtual key, not the master key -> admin rejects it.
    r = await app_client.get("/admin/providers")
    assert r.status_code == 401
    r2 = await app_client.get("/admin/providers", headers={"Authorization": "Bearer test-master"})
    assert r2.status_code == 200
