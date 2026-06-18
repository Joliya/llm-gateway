# Architecture

llm-gateway is a single FastAPI application that presents an OpenAI-compatible
surface (`/v1/*`), proxies requests to upstream LLM providers, and is configured
at runtime through a management API (`/admin/*`) and a static admin console
(`/ui/`).

## Request path (`POST /v1/chat/completions`)

```
client
  │  Bearer <virtual key>
  ▼
RequestContextMiddleware ── stamps X-Request-Id, audits admin mutations
  ▼
authenticate_virtual_key ── resolves the key, checks enabled/expiry
  ▼
budget.reset_if_needed / is_over_budget ── per-key spend window + cap
  ▼
router.resolve ── alias → deployments, or `provider/model` prefix route
  ▼
ChatExecutor.run ── load-balance, rate-limit, param-transform, call upstream,
  │                 retry/fallback, circuit-breaker
  ▼
cost.compute_cost → budget.add_spend (atomic SQL increment)
  ▼
logging_service.log_request → metrics.record_request
  │                            (+ async queue → batch DB write)
  ▼
response to client (X-Request-Id echoed)
```

## Key modules

| Area | Module | Notes |
|------|--------|-------|
| Config | `app/config.py` | pydantic-settings, env prefix `GW_` |
| Models | `app/db/models.py` | SQLAlchemy 2.0 async; Alembic migrations in `alembic/` |
| Config snapshot | `app/core/config_store.py` | in-memory cache of DB config (TTL `GW_CONFIG_CACHE_TTL_SECONDS`) |
| Routing | `app/core/router.py` | alias resolution + `provider/model` prefix routing |
| Execution | `app/core/executor.py` | retries, fallback chains, streaming |
| Load balancing | `app/core/load_balancer.py` | round_robin / weighted / least_busy / random |
| Resilience | `app/core/circuit_breaker.py`, `app/core/rate_limiter.py` | per-pod CB; memory or Redis rate limits |
| Provider adapters | `app/providers/*` | `openai_compat`, `anthropic`, `gemini` |
| Param translation | `app/transform/*` | drop/default/pin params; cross-provider `reasoning_effort` |
| Cost & budget | `app/core/cost.py`, `app/core/budget.py` | per-1M-token pricing; atomic spend; period reset sweep |
| Observability | `app/core/logging_service.py`, `app/core/metrics.py`, `app/core/request_context.py` | request log, Prometheus `/metrics`, correlation id |
| Audit | `app/core/middleware.py` + `AdminAuditLog` | records mutating `/admin` calls |
| Console | `app/web/static/*` | vanilla JS, no build step, EN/中文 |

## Data model

- **Provider** — an upstream vendor; its `name` doubles as a routing prefix, and
  `model_prices` is a price book used to cost prefix routes without a deployment.
- **Credential** — an API key (+ endpoint overrides) under a provider, Fernet-encrypted.
- **Alias** — a client-facing model name = a load-balancing group, with an
  optional fallback chain.
- **Deployment** — `(alias, credential, upstream_model)` plus params and pricing.
- **VirtualKey** — a downstream-issued key with allowlist, RPM/TPM limits, and a
  budget (`total`/`daily`/`monthly`).
- **RequestLog** — one row per proxied request (tokens, cost, latency, captured
  upstream I/O, correlation id).
- **AdminAuditLog** — one row per mutating admin call.

## Observability

- `GET /metrics` — Prometheus: `gw_requests_total`, `gw_request_latency_seconds`,
  `gw_tokens_total`, `gw_cost_total`, `gw_cache_hits_total`, and async-log queue
  gauges. Toggle with `GW_METRICS_ENABLED`.
- `X-Request-Id` — accepted from the client or generated, echoed in the response
  and stored on each request log for tracing.
- Request logs are written off the request path by a background batch worker
  (`GW_LOG_ASYNC`, ignored on SQLite). See `docs/USAGE.md` for the knobs.

## Scaling notes

The app is stateless except for *per-pod best-effort* state (circuit breaker,
least_busy in-flight counts). For horizontal scaling:

- Set `GW_REDIS_URL` so rate limits and the response cache are shared.
- Run migrations once (a Job / initContainer) and set `GW_RUN_MIGRATIONS=false`
  on app pods — see `deploy/k8s/`.
- Size the DB pool so `replicas × (GW_DB_POOL_SIZE + GW_DB_MAX_OVERFLOW)` stays
  under the database's connection limit (or front it with PgBouncer).
