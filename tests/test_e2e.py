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
async def test_logs_filters(app_client):
    import datetime as dt

    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 200, r.text

    async def q(**params):
        resp = await app_client.get("/admin/logs", params=params, headers=MASTER_HEADERS)
        assert resp.status_code == 200, resp.text
        return resp.json()

    # fuzzy model matches requested_model / alias ("balanced"); no match → empty
    assert len(await q(model="bala")) == 1
    assert await q(model="nomatch") == []
    # LIKE wildcards in the term are matched literally, not as patterns
    assert await q(model="bal%") == []
    # provider is an exact match on provider_name ("mockoai")
    assert len(await q(provider="mockoai")) == 1
    assert await q(provider="mock") == []          # not a substring match
    assert await q(provider="zzz") == []
    # time range
    past = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).isoformat()
    future = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
    assert len(await q(start=past)) == 1
    assert await q(start=future) == []
    assert len(await q(end=future)) == 1
    assert await q(end=past) == []


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
async def test_cost_response_header_matches_logged_cost(app_client):
    # litellm convention: emit cost as x-litellm-response-cost so litellm-based
    # clients (dspy.LM) forward it into Opik with no client change.
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())  # usage 10/5, gpt-x priced 1/2
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "mockoai/gpt-x", "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text
    header_cost = float(r.headers["x-litellm-response-cost"])  # must parse as float
    assert header_cost == pytest.approx(2e-5)  # 10/1e6*1 + 5/1e6*2
    logs = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()
    assert header_cost == pytest.approx(logs[0]["cost"])


@respx.mock
async def test_log_records_resolved_provider_and_credential(app_client):
    # "balanced" load-balances over two credentials (c1/c2) of provider mockoai;
    # the log must pin down which provider + credential actually served it.
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response())
    )
    r = await app_client.post("/v1/chat/completions", json={
        "model": "balanced", "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 200, r.text

    log = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()[0]
    assert log["provider_name"] == "mockoai"
    assert log["credential_id"] is not None  # the concrete credential picked by LB
    detail = (await app_client.get(f"/admin/logs/{log['id']}", headers=MASTER_HEADERS)).json()
    assert detail["provider_name"] == "mockoai"
    assert detail["credential_id"] == log["credential_id"]


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


@respx.mock
async def test_playground_chat_can_target_one_deployment(app_client):
    deployments = (await app_client.get(
        "/admin/deployments",
        headers={"Authorization": "Bearer test-master"},
    )).json()
    deployment_id = deployments[1]["id"]
    respx.post("https://up.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_response("pong"))
    )

    r = await app_client.post(
        "/admin/playground/chat",
        headers={"Authorization": "Bearer test-master"},
        json={"deployment_id": deployment_id, "messages": [{"role": "user", "content": "ping"}]},
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["meta"]["alias"] == "balanced"
    assert data["meta"]["deployment_id"] == deployment_id
    assert data["meta"]["retries"] == 0
    assert respx.calls.last.request.headers["authorization"] == "Bearer k2"


async def test_playground_chat_requires_one_target(app_client):
    deployments = (await app_client.get(
        "/admin/deployments",
        headers={"Authorization": "Bearer test-master"},
    )).json()
    r = await app_client.post(
        "/admin/playground/chat",
        headers={"Authorization": "Bearer test-master"},
        json={
            "model": "balanced",
            "deployment_id": deployments[0]["id"],
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert r.status_code == 400


async def test_playground_chat_unknown_deployment(app_client):
    r = await app_client.post(
        "/admin/playground/chat",
        headers={"Authorization": "Bearer test-master"},
        json={"deployment_id": 999999, "messages": [{"role": "user", "content": "ping"}]},
    )
    assert r.status_code == 404


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


async def test_non_master_user_cannot_manage_users(app_client):
    # A logged-in user has admin access but must NOT manage other users.
    created = (await app_client.post("/admin/users", headers=MASTER_HEADERS,
                                     json={"username": "erin"})).json()
    uid = created["id"]
    login = await app_client.post("/admin/login",
                                  json={"username": "erin", "password": created["password"]})
    H = {"Authorization": f"Bearer {login.json()['token']}"}

    # ordinary admin endpoints still work for the user
    assert (await app_client.get("/admin/keys", headers=H)).status_code == 200
    # but user management is master-only -> 403
    assert (await app_client.get("/admin/users", headers=H)).status_code == 403
    assert (await app_client.post("/admin/users", headers=H, json={"username": "x"})).status_code == 403
    assert (await app_client.post(f"/admin/users/{uid}/reset-password", headers=H)).status_code == 403
    assert (await app_client.delete(f"/admin/users/{uid}", headers=H)).status_code == 403


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


# --- responses / images / audio passthrough endpoints ---

@respx.mock
async def test_responses_passthrough_and_cost(app_client):
    respx.post("https://up.test/v1/responses").mock(return_value=httpx.Response(200, json={
        "id": "resp_1", "object": "response", "model": "gpt-x", "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }))
    r = await app_client.post("/v1/responses", json={
        "model": "balanced", "input": "hello",
    })
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "resp_1"
    # 10/1e6*1 + 5/1e6*2 = 2e-5 (gpt-x priced 1/2), mapped from input/output tokens
    assert float(r.headers["x-litellm-response-cost"]) == pytest.approx(2e-5)


@respx.mock
async def test_responses_streaming_logs_usage(app_client):
    sse = (
        'data: {"type":"response.output_text.delta","delta":"Hel"}\n\n'
        'data: {"type":"response.completed","response":{"usage":'
        '{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}\n\n'
    )
    respx.post("https://up.test/v1/responses").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    r = await app_client.post("/v1/responses", json={
        "model": "balanced", "input": "hi", "stream": True,
    })
    assert r.status_code == 200
    assert "response.completed" in r.text
    log = (await app_client.get("/admin/logs?limit=1", headers=MASTER_HEADERS)).json()[0]
    assert log["cost"] == pytest.approx(2e-5)  # usage parsed from the terminal event
    assert log["provider_name"] == "mockoai"


@respx.mock
async def test_images_generations_passthrough(app_client):
    respx.post("https://up.test/v1/images/generations").mock(return_value=httpx.Response(200, json={
        "created": 1, "data": [{"url": "https://img.test/a.png"}],
    }))
    r = await app_client.post("/v1/images/generations", json={
        "model": "balanced", "prompt": "a cat", "n": 1,
    })
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["url"] == "https://img.test/a.png"
    assert float(r.headers["x-litellm-response-cost"]) == 0.0  # no usage -> cost 0


@respx.mock
async def test_audio_transcriptions_multipart(app_client):
    captured = {}

    def handler(request: httpx.Request):
        captured["ctype"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "hello world"})

    respx.post("https://up.test/v1/audio/transcriptions").mock(side_effect=handler)
    r = await app_client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"\x00\x01\x02RIFF", "audio/wav")},
        data={"model": "balanced", "response_format": "json"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "hello world"
    assert captured["ctype"].startswith("multipart/form-data")  # forwarded as multipart


@respx.mock
async def test_audio_speech_binary(app_client):
    audio_bytes = b"ID3\x04\x00\x00fake-mp3-bytes"
    respx.post("https://up.test/v1/audio/speech").mock(
        return_value=httpx.Response(200, content=audio_bytes, headers={"content-type": "audio/mpeg"})
    )
    r = await app_client.post("/v1/audio/speech", json={
        "model": "balanced", "input": "hello", "voice": "alloy",
    })
    assert r.status_code == 200, r.text
    assert r.content == audio_bytes
    assert r.headers["content-type"] == "audio/mpeg"


# --- Settings: currency / exchange rates ---
async def test_settings_defaults_returned(app_client):
    from app.core import fx

    r = await app_client.get("/admin/settings", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    cur = r.json()["currency"]
    # fresh install ships with a seeded common-currency set, base/display = USD
    assert cur["display"] == "USD"
    assert "CNY" in cur["rates"] and cur["rates"]
    assert cur == fx.DEFAULT_CURRENCY


async def test_settings_currency_roundtrip_and_normalization(app_client):
    # lower-case codes are upper-cased; display must be a known currency.
    r = await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                             json={"rates": {"cny": 7.15, "eur": 0.92}, "display": "cny"})
    assert r.status_code == 200, r.text
    assert r.json()["currency"] == {"rates": {"CNY": 7.15, "EUR": 0.92}, "display": "CNY"}
    # persisted
    got = (await app_client.get("/admin/settings", headers=MASTER_HEADERS)).json()
    assert got["currency"]["display"] == "CNY"


async def test_settings_currency_rejects_bad_input(app_client):
    bad_display = await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                                       json={"rates": {"CNY": 7.15}, "display": "JPY"})
    assert bad_display.status_code == 400
    bad_rate = await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                                    json={"rates": {"CNY": -1}, "display": "USD"})
    assert bad_rate.status_code == 400


async def test_settings_unknown_key_404(app_client):
    r = await app_client.put("/admin/settings/nope", headers=MASTER_HEADERS, json={"x": 1})
    assert r.status_code == 404


# --- FX: daily exchange-rate auto-update ---
@respx.mock
async def test_currency_refresh_uses_primary_source(app_client):
    await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                         json={"rates": {"CNY": 1.0, "EUR": 1.0}, "display": "CNY"})
    respx.get("https://open.er-api.com/v6/latest/USD").mock(
        return_value=httpx.Response(200, json={"result": "success",
                                               "rates": {"CNY": 7.2, "EUR": 0.93, "JPY": 150}}))
    r = await app_client.post("/admin/settings/currency/refresh", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True
    # only configured currencies are updated; new ones (JPY) are NOT added
    assert body["currency"]["rates"] == {"CNY": 7.2, "EUR": 0.93}
    assert body["currency"]["display"] == "CNY"


@respx.mock
async def test_currency_refresh_falls_back_when_primary_down(app_client):
    await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                         json={"rates": {"CNY": 1.0}, "display": "USD"})
    respx.get("https://open.er-api.com/v6/latest/USD").mock(return_value=httpx.Response(500))
    respx.get("https://api.frankfurter.app/latest?base=USD").mock(
        return_value=httpx.Response(200, json={"base": "USD", "rates": {"CNY": 7.05}}))
    r = await app_client.post("/admin/settings/currency/refresh", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["currency"]["rates"]["CNY"] == 7.05


@respx.mock
async def test_currency_refresh_seeds_defaults_on_fresh_install(app_client):
    # No `currency` row yet → refresh seeds the common-currency defaults with
    # live rates from the source.
    respx.get("https://open.er-api.com/v6/latest/USD").mock(
        return_value=httpx.Response(200, json={"result": "success",
                                               "rates": {"CNY": 7.11, "EUR": 0.9, "GBP": 0.78,
                                                         "JPY": 149, "HKD": 7.79, "KRW": 1340,
                                                         "SGD": 1.34, "AUD": 1.5, "CAD": 1.37}}))
    r = await app_client.post("/admin/settings/currency/refresh", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True
    assert body["currency"]["rates"]["CNY"] == 7.11
    assert body["currency"]["display"] == "USD"


@respx.mock
async def test_currency_reset_defaults_seeds_common_set(app_client):
    from app.core import fx

    # an existing install with an empty (operator-cleared) currency row
    await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                         json={"rates": {}, "display": "USD"})
    respx.get("https://open.er-api.com/v6/latest/USD").mock(
        return_value=httpx.Response(200, json={"result": "success", "rates": {"CNY": 7.05}}))
    r = await app_client.post("/admin/settings/currency/reset-defaults", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    rates = r.json()["currency"]["rates"]
    # every common currency is present; reachable ones get the live rate
    assert set(rates) == set(fx.DEFAULT_RATES)
    assert rates["CNY"] == 7.05


@respx.mock
async def test_currency_reset_defaults_offline_keeps_placeholders(app_client):
    from app.core import fx

    # every source down → live fetch fails; placeholder defaults must still stand
    for _, url, _ in fx.SOURCES:
        respx.get(url).mock(return_value=httpx.Response(500))
    r = await app_client.post("/admin/settings/currency/reset-defaults", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["currency"]["rates"] == fx.DEFAULT_RATES


@respx.mock
async def test_currency_refresh_noop_when_cleared(app_client):
    # Operator explicitly cleared all currencies → respected, no re-seed.
    await app_client.put("/admin/settings/currency", headers=MASTER_HEADERS,
                         json={"rates": {}, "display": "USD"})
    r = await app_client.post("/admin/settings/currency/refresh", headers=MASTER_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] is False
