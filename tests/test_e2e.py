from __future__ import annotations

import json

import httpx
import pytest
import respx

from tests.conftest import MASTER_HEADERS


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
async def test_log_captures_upstream_io(app_client):
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced",
        "messages": [{"role": "user", "content": "hello"}],
        "reasoning_effort": "high",
    })
    assert r.status_code == 200, r.text

    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs and logs[0]["has_upstream_io"] is True
    detail = (await app_client.get(f"/admin/logs/{logs[0]['id']}", headers=MASTER_HEADERS)).json()
    # exact body sent upstream is recorded — upstream model + reasoning level
    assert detail["upstream_request"]["model"] == "gpt-x"
    assert detail["upstream_request"]["reasoning_effort"] == "high"
    assert detail["upstream_url"] == "https://up.test/v1/chat/completions"
    # raw provider response is recorded too
    assert detail["upstream_response"]["choices"][0]["message"]["content"] == "hi"


@respx.mock
async def test_log_captures_upstream_error(app_client):
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(401, text='{"error":"bad key"}')
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 401

    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs and logs[0]["status"] == 401
    assert logs[0]["alias"] == "balanced"            # failed attempt keeps routing info
    detail = (await app_client.get(f"/admin/logs/{logs[0]['id']}", headers=MASTER_HEADERS)).json()
    assert "bad key" in json.dumps(detail["upstream_response"])


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
async def test_prefix_routing_inherits_pricing(app_client):
    # mockoai/gpt-x: no DB deployment row, but gpt-x is priced (1.0/2.0) on the
    # provider's deployments, so the prefix route should still be costed.
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())  # usage 10/5
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "mockoai/gpt-x", "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text

    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs and logs[0]["alias"] == "mockoai/gpt-x"
    # 10/1e6*1 + 5/1e6*2 = 2e-5
    assert logs[0]["cost"] > 0


@respx.mock
async def test_prefix_routing_costed_from_provider_price_book(app_client):
    # A model with NO deployment row at all — priced only via the provider's
    # model_prices book. Prefix routing must still bill it.
    provs = (await app_client.get("/admin/providers", headers=MASTER_HEADERS)).json()
    pid = next(p["id"] for p in provs if p["name"] == "mockoai")
    r = await app_client.patch(f"/admin/providers/{pid}", headers=MASTER_HEADERS,
                               json={"model_prices": {"priced-only": {"input": 3.0, "output": 6.0}}})
    assert r.status_code == 200, r.text

    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())  # usage 10/5
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "mockoai/priced-only", "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text

    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs and logs[0]["alias"] == "mockoai/priced-only"
    # 10/1e6*3 + 5/1e6*6 = 6e-5
    assert logs[0]["cost"] > 0


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
async def test_streaming_requests_usage_and_logs_cost(app_client):
    captured = {}
    sse = (
        'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    respx.post("https://up.test/v1/chat/completions").mock(side_effect=handler)

    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "x"}], "stream": True,
    })
    assert r.status_code == 200
    # gateway asks the upstream for the trailing usage chunk
    assert captured["body"]["stream_options"]["include_usage"] is True

    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs and logs[0]["total_tokens"] == 30
    # prices are 1.0 / 2.0 per 1M tokens -> 10/1e6*1 + 20/1e6*2 = 5e-5
    assert logs[0]["cost"] > 0


@respx.mock
async def test_models_listing(app_client):
    r = await app_client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "balanced" in ids


async def test_request_logger_async_batches_writes(app_client):
    # Drive a RequestLogger directly (the app_client fixture only provides the
    # schema). conftest forces sync logging globally, so build + enable one by
    # hand to exercise the async enqueue -> worker -> batch-write path.
    import asyncio

    from sqlalchemy import select

    from app.core.config_store import ResolvedDeployment  # noqa: F401 (ensures import path)
    from app.core.logging_service import RequestLogger, _build_payload
    from app.db.models import RequestLog
    from app.db.session import SessionLocal
    from app.providers.base import Usage

    rl = RequestLogger()
    rl._queue = asyncio.Queue(maxsize=100)
    rl._enabled = True
    rl._task = asyncio.create_task(rl._worker())

    payload = _build_payload(
        virtual_key_id=None, requested_model="async-logged", deployment=None,
        usage=Usage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
        status=200, cost=0.5, latency_ms=12, retries=0, cache_hit=False,
        error=None, upstream_url="https://up.test/v1/chat/completions",
        upstream_request={"model": "m"}, upstream_response={"ok": True},
    )
    assert rl.enqueue(payload) is True
    await rl.drain()       # block until the worker has written it
    await rl.stop()

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(RequestLog).where(RequestLog.requested_model == "async-logged")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].cost == 0.5
    assert rows[0].total_tokens == 7
    assert rows[0].upstream_request == {"model": "m"}


async def test_request_logger_drops_when_full():
    # Full queue must drop + count, never block the caller.
    import asyncio

    from app.core.logging_service import RequestLogger

    rl = RequestLogger()
    rl._queue = asyncio.Queue(maxsize=1)
    rl._enabled = True   # no worker consuming, so the queue stays full
    assert rl.enqueue({"requested_model": "a"}) is True    # fills the single slot
    assert rl.enqueue({"requested_model": "b"}) is False   # dropped
    assert rl.enqueue({"requested_model": "c"}) is False   # dropped
    assert rl.dropped == 2


async def test_add_spend_is_atomic_no_lost_update(app_client):
    # Two sessions each load their own (soon-stale) copy of the same key and
    # both add spend. A read-modify-write on the ORM object would lose one
    # update; the SQL-side `spend = spend + cost` must accumulate both.
    from sqlalchemy import select

    from app.core import budget as budget_mod
    from app.db.models import VirtualKey
    from app.db.session import SessionLocal

    async with SessionLocal() as s:
        vk = (await s.execute(select(VirtualKey))).scalars().first()
        vk_id = vk.id
        start = vk.spend or 0.0

    async with SessionLocal() as a, SessionLocal() as b:
        vk_a = await a.get(VirtualKey, vk_id)
        vk_b = await b.get(VirtualKey, vk_id)   # both read the same starting spend
        await budget_mod.add_spend(a, vk_a, 1.5)
        await a.commit()
        await budget_mod.add_spend(b, vk_b, 2.5)
        await b.commit()

    async with SessionLocal() as s:
        vk = await s.get(VirtualKey, vk_id)
        assert vk.spend == pytest.approx(start + 4.0)   # 1.5 + 2.5, nothing lost


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


@respx.mock
async def test_request_id_echoed_and_logged(app_client):
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())
    )
    r = await app_client.post(
        "/v1/chat/completions",
        headers={"X-Request-Id": "trace-abc-123"},
        json={"model": "balanced", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 200, r.text
    # client-supplied id is echoed back
    assert r.headers["x-request-id"] == "trace-abc-123"
    # ...and stamped on the request log
    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert logs[0]["request_id"] == "trace-abc-123"


async def test_request_id_generated_when_absent(app_client):
    r = await app_client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")   # a fresh id was generated


async def test_user_create_login_and_audit_actor(app_client):
    # master creates a user; password is returned once
    created = (await app_client.post("/admin/users", headers=MASTER_HEADERS,
                                     json={"username": "alice"})).json()
    assert created["username"] == "alice"
    pw = created["password"]
    assert pw and "password" not in {k for u in
                                     (await app_client.get("/admin/users", headers=MASTER_HEADERS)).json()
                                     for k in u}

    # user logs in and gets a session token
    login = await app_client.post("/admin/login", json={"username": "alice", "password": pw})
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    H = {"Authorization": f"Bearer {token}"}

    # the session token authorizes the admin API
    assert (await app_client.get("/admin/providers", headers=H)).status_code == 200

    # a mutation made via the session is attributed to the username in the audit log
    await app_client.post("/admin/keys", headers=H,
                          json={"name": "via-alice", "allowed_aliases": ["balanced"]})
    audit = (await app_client.get("/admin/audit", headers=MASTER_HEADERS)).json()
    assert any(a["path"] == "/admin/keys" and a["actor"] == "alice" for a in audit)


async def test_user_login_bad_password_and_duplicate(app_client):
    created = (await app_client.post("/admin/users", headers=MASTER_HEADERS,
                                     json={"username": "bob"})).json()
    bad = await app_client.post("/admin/login", json={"username": "bob", "password": "wrong"})
    assert bad.status_code == 401
    dup = await app_client.post("/admin/users", headers=MASTER_HEADERS, json={"username": "bob"})
    assert dup.status_code == 409
    _ = created


async def test_user_reset_password_invalidates_old(app_client):
    created = (await app_client.post("/admin/users", headers=MASTER_HEADERS,
                                     json={"username": "carol"})).json()
    uid, old_pw = created["id"], created["password"]
    assert (await app_client.post("/admin/login",
                                  json={"username": "carol", "password": old_pw})).status_code == 200

    reset = await app_client.post(f"/admin/users/{uid}/reset-password", headers=MASTER_HEADERS)
    new_pw = reset.json()["password"]
    assert new_pw != old_pw
    assert (await app_client.post("/admin/login",
                                  json={"username": "carol", "password": old_pw})).status_code == 401
    assert (await app_client.post("/admin/login",
                                  json={"username": "carol", "password": new_pw})).status_code == 200


async def test_disabled_user_cannot_login_or_use_session(app_client):
    created = (await app_client.post("/admin/users", headers=MASTER_HEADERS,
                                     json={"username": "dave"})).json()
    uid, pw = created["id"], created["password"]
    token = (await app_client.post("/admin/login",
                                   json={"username": "dave", "password": pw})).json()["token"]
    H = {"Authorization": f"Bearer {token}"}
    assert (await app_client.get("/admin/providers", headers=H)).status_code == 200

    # disable the user
    await app_client.patch(f"/admin/users/{uid}", headers=MASTER_HEADERS, json={"enabled": False})
    # existing session token is now rejected, and re-login fails
    assert (await app_client.get("/admin/providers", headers=H)).status_code == 401
    assert (await app_client.post("/admin/login",
                                  json={"username": "dave", "password": pw})).status_code == 401


async def test_readiness_checks_database(app_client):
    r = await app_client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    # no Redis configured in tests -> not reported
    assert "redis" not in body["checks"]


@respx.mock
async def test_metrics_endpoint_counts_requests(app_client):
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())
    )
    await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "x"}],
    })
    r = await app_client.get("/metrics")
    assert r.status_code == 200
    assert "gw_requests_total" in r.text
    assert 'alias="balanced"' in r.text


async def test_admin_audit_records_mutations(app_client):
    # A mutating admin call is audited; a GET is not.
    await app_client.post("/admin/keys", headers=MASTER_HEADERS,
                          json={"name": "audited", "allowed_aliases": ["balanced"]})
    audit = (await app_client.get("/admin/audit", headers=MASTER_HEADERS)).json()
    assert any(a["method"] == "POST" and a["path"] == "/admin/keys" for a in audit)
    # the read itself must not appear as a mutation
    assert all(a["method"] != "GET" for a in audit)


@respx.mock
async def test_key_rotation_invalidates_old_secret(app_client):
    # issue a key, use it, rotate it, then old fails and new works
    created = (await app_client.post("/admin/keys", headers=MASTER_HEADERS,
                                     json={"name": "rot", "allowed_aliases": ["*"]})).json()
    key_id, old_secret = created["id"], created["key"]

    rotated = (await app_client.post(f"/admin/keys/{key_id}/rotate",
                                     headers=MASTER_HEADERS)).json()
    new_secret = rotated["key"]
    assert new_secret != old_secret

    old = await app_client.get("/v1/models", headers={"Authorization": f"Bearer {old_secret}"})
    assert old.status_code == 401
    new = await app_client.get("/v1/models", headers={"Authorization": f"Bearer {new_secret}"})
    assert new.status_code == 200


async def test_budget_sweep_resets_expired_window(app_client):
    import datetime as dt

    from sqlalchemy import select

    from app.core import budget as budget_mod
    from app.db.models import VirtualKey
    from app.db.session import SessionLocal

    async with SessionLocal() as s:
        vk = (await s.execute(select(VirtualKey))).scalars().first()
        vk.budget_period = "daily"
        vk.spend = 7.5
        vk.budget_anchor = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
        vk_id = vk.id
        await s.commit()

    async with SessionLocal() as s:
        n = await budget_mod.sweep_expired(s)
    assert n >= 1

    async with SessionLocal() as s:
        vk = await s.get(VirtualKey, vk_id)
        assert vk.spend == 0.0
        anchor = vk.budget_anchor
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=dt.UTC)
        assert anchor > dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)  # anchor advanced


@respx.mock
async def test_multimodal_image_content_passthrough(app_client):
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_response())

    respx.post("https://up.test/v1/chat/completions").mock(side_effect=handler)

    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "https://img.test/a.png"}},
    ]
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": content}],
    })
    assert r.status_code == 200, r.text
    # the structured multimodal content survives intact to the upstream
    sent = captured["body"]["messages"][0]["content"]
    assert sent[1]["type"] == "image_url"
    assert sent[1]["image_url"]["url"] == "https://img.test/a.png"


@respx.mock
async def test_tool_calling_roundtrip(app_client):
    captured = {}
    tool_response = {
        "id": "chatcmpl-2", "object": "chat.completion", "created": 1, "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "get_weather",
                                         "arguments": '{"city":"SF"}'}}],
        }}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
    }

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=tool_response)

    respx.post("https://up.test/v1/chat/completions").mock(side_effect=handler)

    tools = [{"type": "function", "function": {
        "name": "get_weather", "description": "get weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    }}]
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "weather in SF?"}],
        "tools": tools, "tool_choice": "auto",
    })
    assert r.status_code == 200, r.text
    # tools forwarded upstream
    assert captured["body"]["tools"][0]["function"]["name"] == "get_weather"
    assert captured["body"]["tool_choice"] == "auto"
    # tool_calls returned to the client
    out = r.json()["choices"][0]["message"]["tool_calls"]
    assert out[0]["function"]["name"] == "get_weather"
