"""Prometheus metrics for the proxy surface.

A single `record_request()` choke point is fed from `log_request`, so every
proxied call — success, upstream error, cache hit — is counted exactly once.
Scrape at `GET /metrics`.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()

REQUESTS = Counter(
    "gw_requests_total",
    "Proxied requests by alias, provider type and HTTP status.",
    ["alias", "provider_type", "status"],
    registry=registry,
)
LATENCY = Histogram(
    "gw_request_latency_seconds",
    "End-to-end proxied request latency.",
    ["alias"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
    registry=registry,
)
TOKENS = Counter(
    "gw_tokens_total",
    "Tokens accounted, by alias and kind (prompt/completion).",
    ["alias", "kind"],
    registry=registry,
)
COST = Counter(
    "gw_cost_total",
    "Accumulated cost by alias (in the configured price unit).",
    ["alias"],
    registry=registry,
)
CACHE_HITS = Counter(
    "gw_cache_hits_total",
    "Responses served from the response cache.",
    ["alias"],
    registry=registry,
)

# Sampled at scrape time from the async request logger (see /metrics endpoint).
LOG_QUEUE_DEPTH = Gauge(
    "gw_log_queue_depth",
    "Pending rows in the async request-log queue.",
    registry=registry,
)
LOG_DROPPED = Gauge(
    "gw_log_dropped_total",
    "Request-log rows dropped because the async queue was full.",
    registry=registry,
)
LOG_WRITE_ERRORS = Gauge(
    "gw_log_write_errors_total",
    "Failed async request-log batch writes.",
    registry=registry,
)


def record_request(
    *,
    alias: str | None,
    provider_type: str | None,
    status: int,
    cost: float,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    cache_hit: bool,
) -> None:
    a = alias or "-"
    REQUESTS.labels(a, provider_type or "-", str(status)).inc()
    LATENCY.labels(a).observe(latency_ms / 1000.0)
    if prompt_tokens:
        TOKENS.labels(a, "prompt").inc(prompt_tokens)
    if completion_tokens:
        TOKENS.labels(a, "completion").inc(completion_tokens)
    if cost:
        COST.labels(a).inc(cost)
    if cache_hit:
        CACHE_HITS.labels(a).inc()


def render() -> bytes:
    """Refresh the sampled gauges from the logger, then serialize."""
    from app.core.logging_service import request_logger

    LOG_QUEUE_DEPTH.set(request_logger.queue_depth)
    LOG_DROPPED.set(request_logger.dropped)
    LOG_WRITE_ERRORS.set(request_logger.write_errors)
    return generate_latest(registry)
