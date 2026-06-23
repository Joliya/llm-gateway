# llm-gateway

[![CI](https://github.com/Joliya/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Joliya/llm-gateway/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A lightweight, self-hosted **LLM proxy** — a simpler alternative to LiteLLM.
Exposes an OpenAI-compatible API, manages multiple providers/credentials from a
database, and adds load balancing, rate limiting, fallbacks, param pinning,
virtual keys with budgets, usage/cost logging, circuit breaking, and caching.

## Features

- **OpenAI-compatible surface**: `/v1/chat/completions` (incl. streaming),
  `/v1/completions`, `/v1/embeddings`, `/v1/responses` (incl. streaming),
  `/v1/images/generations`, `/v1/audio/transcriptions`, `/v1/audio/speech`,
  `/v1/models`.
- **Multi-provider adapters**: `openai_compat` (OpenAI, Kimi/Moonshot, DeepSeek,
  通义/DashScope, Volcengine/Doubao, vLLM, …), `anthropic`, `gemini`. Params are
  transformed to each vendor's format and responses normalized back to OpenAI.
- **Multimodal images**: send OpenAI-style `image_url` blocks to any vision model.
  The gateway maps them into Anthropic/Gemini native shapes, and downloads remote
  image URLs to base64 for providers that can't fetch them (Kimi, Gemini).
- **Alias load balancing**: group multiple deployments under one alias with
  `round_robin` / `weighted` / `least_busy` / `random`.
- **Prefix routing**: call `provider/model` (e.g. `openai/gpt-4o`,
  `kimi/moonshot-v1-8k`) to route by provider with on-the-fly param conversion.
- **Param policy per deployment**: `drop` → `default` → `pinned` (hard-coded
  params always win).
- **Rate limiting**: RPM/TPM per virtual key, deployment, and credential.
- **Fallback + retry**: retry within the alias pool, then walk the alias's
  fallback chain, with exponential backoff.
- **Circuit breaking**: cool down unhealthy deployments automatically.
- **Virtual keys**: issue per-client keys with allowed models, RPM/TPM limits,
  and total/daily/monthly budgets.
- **Usage & cost logging**: per-request token/cost/latency logs + summaries, plus
  an **Analytics** console view (spend/requests by alias and by key).
- **Observability**: Prometheus `/metrics`, `X-Request-Id` correlation ids, and an
  **admin audit log** of every mutating `/admin` call (attributed to the actor).
- **Console users**: create operator accounts that log in with a username +
  auto-generated password (managed by the master key); the master can reset any
  password. Logged-in users get full admin access; audit logs record who acted.
- **Key management**: rotate a virtual key's secret in place; daily/monthly budget
  windows reset automatically (idle keys swept in the background too).
- **Response caching**: optional, per-alias overridable.
- **Storage**: SQLite + in-memory out of the box; switch to Postgres + Redis via
  env vars for multi-instance deployments.

## Quick start (local)

```bash
cd llm-gateway
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set GW_MASTER_KEY at least
uvicorn app.main:app --reload   # tables auto-created on startup
```

### Web console

Open **http://localhost:8000/** (redirects to `/ui/`) and authenticate with your
`GW_MASTER_KEY`. The console is a self-contained static app served by the gateway
itself — no build step, no CDN, works air-gapped. From it you can manage
providers, credentials, aliases, deployments, and virtual keys, and watch traffic,
spend, and circuit-breaker health. Newly issued virtual keys are revealed once.

The **Playground** tab sends a request through the real routing path (load
balancing, param pinning, fallback, the actual upstream call) and reports which
deployment served it — tokens, cost, latency, and retries. It's authenticated by
the master key, so it bypasses virtual-key budgets and limits.

### Configure via the admin API

```bash
M="Authorization: Bearer $GW_MASTER_KEY"

# 1. provider
curl -s localhost:8000/admin/providers -H "$M" -H 'content-type: application/json' \
  -d '{"name":"openai","provider_type":"openai_compat","default_base_url":"https://api.openai.com/v1"}'

# 2. credential (api key encrypted at rest)
curl -s localhost:8000/admin/credentials -H "$M" -H 'content-type: application/json' \
  -d '{"provider_id":1,"name":"key1","api_key":"sk-..."}'

# 3. alias (load-balancing group)
curl -s localhost:8000/admin/aliases -H "$M" -H 'content-type: application/json' \
  -d '{"name":"gpt-4o-balanced","lb_strategy":"round_robin","fallback_aliases":[]}'

# 4. deployment (alias + credential + upstream model, with pinned params)
curl -s localhost:8000/admin/deployments -H "$M" -H 'content-type: application/json' \
  -d '{"alias_id":1,"credential_id":1,"upstream_model":"gpt-4o","pinned_params":{"temperature":0},"input_price":2.5,"output_price":10}'

# 5. issue a virtual key (returned once)
curl -s localhost:8000/admin/keys -H "$M" -H 'content-type: application/json' \
  -d '{"name":"team-a","allowed_aliases":["*"],"rpm_limit":60,"max_budget":50,"budget_period":"monthly"}'
```

### Call it like OpenAI

```bash
curl localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <virtual-key>" -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-balanced","messages":[{"role":"user","content":"hi"}]}'
```

`model` accepts either a configured **alias** or a **`provider/model`** prefix.

> 📖 完整使用文档（管理 API 接入、OpenAI SDK 客户端接入、UI 配置负载均衡）见
> [`docs/USAGE.md`](docs/USAGE.md)。

## Production

- Set `GW_DATABASE_URL=postgresql+asyncpg://…` and `GW_REDIS_URL=redis://…`.
- Set `GW_AUTO_CREATE_TABLES=false` and let Alembic own the schema:
  `alembic upgrade head` (idempotent; safe to run on every deploy).
- `docker compose up` bundles Postgres + Redis and runs `alembic upgrade head`
  automatically via the image entrypoint before starting the server.

### Schema migrations

The schema is versioned with Alembic (`alembic/versions/`). The initial
migration creates all tables.

```bash
alembic upgrade head            # apply migrations (uses GW_DATABASE_URL)
alembic downgrade -1            # roll back one revision
alembic current                 # show the applied revision

# after changing app/db/models.py:
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

In development, `GW_AUTO_CREATE_TABLES=true` (the default) creates tables on
startup so you can skip migrations entirely. Use one or the other, not both.

## Observability

- `GET /metrics` — Prometheus metrics (requests, latency, tokens, cost, cache
  hits, async-log queue). Disable with `GW_METRICS_ENABLED=false`.
- Every response carries an `X-Request-Id` (accepted from the client or
  generated) that's also stored on each request log for tracing.
- Mutating `/admin` calls are recorded to an audit log (`GET /admin/audit`); send
  an `X-Admin-Actor` header to label who made the change.

## Tests

```bash
pip install -e ".[dev]"
ruff check app tests
pytest
```

## Architecture

```
client → auth(virtual key) → budget → rate limit → router(alias | provider/model)
       → load balancer → circuit breaker → param transform → provider adapter (httpx)
       → [on failure: retry in pool → fallback chain] → normalize → log/cost/cache → client
```

Key modules live under `app/core/` (router, load_balancer, rate_limiter,
executor, circuit_breaker, budget, cache, cost), `app/providers/` (vendor
adapters), and `app/api/` (`v1/` proxy + `admin/` management).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a deeper tour and
[`CONTRIBUTING.md`](CONTRIBUTING.md) to get set up for development.

## License

[MIT](LICENSE).
