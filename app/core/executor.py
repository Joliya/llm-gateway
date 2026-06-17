from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import router as router_mod
from app.core.circuit_breaker import circuit_breaker
from app.core.config_store import ResolvedAlias, ResolvedDeployment
from app.core.load_balancer import decr_inflight, incr_inflight, order_deployments
from app.core.rate_limiter import rate_limiter
from app.providers.base import Usage
from app.providers.registry import get_adapter
from app.transform.params import apply_param_rules

_settings = get_settings()


class UpstreamError(Exception):
    def __init__(self, status_code: int, body: str, retryable: bool):
        super().__init__(f"upstream {status_code}")
        self.status_code = status_code
        self.body = body
        self.retryable = retryable


class AllAttemptsFailed(Exception):
    def __init__(self, status_code: int, body: str, *, deployment: Optional[ResolvedDeployment] = None,
                 upstream_url: Optional[str] = None, upstream_request: Optional[dict[str, Any]] = None,
                 upstream_response: Any = None):
        super().__init__(body)
        self.status_code = status_code
        self.body = body
        self.deployment = deployment
        self.upstream_url = upstream_url
        self.upstream_request = upstream_request
        self.upstream_response = upstream_response


@dataclass
class ChatResult:
    response: dict[str, Any]
    deployment: ResolvedDeployment
    usage: Usage
    retries: int
    upstream_url: Optional[str] = None
    upstream_request: Optional[dict[str, Any]] = None
    upstream_response: Any = None


@dataclass
class StreamResult:
    chunks: AsyncIterator[dict[str, Any]]
    deployment: ResolvedDeployment
    # usage is populated as the stream is consumed
    usage: Usage = field(default_factory=Usage)
    retries: int = 0
    upstream_url: Optional[str] = None
    upstream_request: Optional[dict[str, Any]] = None


async def build_candidate_aliases(session: AsyncSession, model: str) -> list[ResolvedAlias]:
    """Primary alias first, then its fallback chain (breadth-first, dedup)."""
    visited: set[str] = set()
    queue: list[str] = [model]
    out: list[ResolvedAlias] = []
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        try:
            alias = await router_mod.resolve(session, name)
        except router_mod.RouteNotFound:
            if not out:
                raise
            continue
        out.append(alias)
        queue.extend(alias.fallback_aliases)
    return out


async def _rate_limit_ok(vk_id: Optional[int], dep: ResolvedDeployment, vk_rpm, vk_tpm) -> bool:
    """Pre-flight RPM checks (+ TPM not-yet-exceeded). Tokens are recorded after."""
    checks = [
        (f"dep:{dep.deployment_id}:rpm", dep.rpm_limit, 1),
        (f"cred:{dep.credential_id}:rpm", dep.cred_rpm_limit, 1),
        (f"dep:{dep.deployment_id}:tpm", dep.tpm_limit, 0),
        (f"cred:{dep.credential_id}:tpm", dep.cred_tpm_limit, 0),
    ]
    if vk_id is not None:
        checks.append((f"vk:{vk_id}:rpm", vk_rpm, 1))
        checks.append((f"vk:{vk_id}:tpm", vk_tpm, 0))
    for key, limit, amount in checks:
        if not await rate_limiter.check(key, limit, amount):
            return False
    return True


async def record_tokens(vk_id: Optional[int], dep: ResolvedDeployment, total_tokens: int) -> None:
    await rate_limiter.add(f"dep:{dep.deployment_id}:tpm", total_tokens)
    await rate_limiter.add(f"cred:{dep.credential_id}:tpm", total_tokens)
    if vk_id is not None:
        await rate_limiter.add(f"vk:{vk_id}:tpm", total_tokens)


def _prepare_params(dep: ResolvedDeployment, body: dict[str, Any]) -> dict[str, Any]:
    return apply_param_rules(
        body,
        drop_params=dep.drop_params,
        default_params=dep.default_params,
        pinned_params=dep.pinned_params,
    )


def _iter_attempts(aliases: list[ResolvedAlias]) -> list[ResolvedDeployment]:
    """Flatten aliases into an ordered try-list, capped per alias by max_retries."""
    attempts: list[ResolvedDeployment] = []
    for alias in aliases:
        ordered = order_deployments(alias.name, alias.deployments, alias.lb_strategy)
        attempts.extend(ordered[: _settings.max_retries + 1])
    return attempts


class ChatExecutor:
    def __init__(self, session: AsyncSession, client: httpx.AsyncClient, vk_id: Optional[int],
                 vk_rpm: Optional[int], vk_tpm: Optional[int]):
        self.session = session
        self.client = client
        self.vk_id = vk_id
        self.vk_rpm = vk_rpm
        self.vk_tpm = vk_tpm

    async def run(self, aliases: list[ResolvedAlias], body: dict[str, Any]) -> ChatResult:
        attempts = _iter_attempts(aliases)
        last_status, last_body = 502, "no upstream available"
        # Track the most recent attempt's upstream I/O so failures are auditable too.
        last_dep: Optional[ResolvedDeployment] = None
        last_url: Optional[str] = None
        last_req: Optional[dict[str, Any]] = None
        last_resp: Any = None
        retries = 0
        for i, dep in enumerate(attempts):
            if i > 0:
                retries += 1
                await asyncio.sleep(_settings.retry_backoff_base * (2 ** (i - 1)))
            if not await _rate_limit_ok(self.vk_id, dep, self.vk_rpm, self.vk_tpm):
                last_status, last_body = 429, "rate limit exceeded"
                continue
            adapter = get_adapter(dep.provider_type)
            params = _prepare_params(dep, body)
            params.pop("stream", None)
            req = adapter.build_chat_request(
                base_url=dep.base_url, api_key=dep.api_key, org=dep.org,
                extra_headers=dep.extra_headers, upstream_model=dep.upstream_model, params=params,
            )
            last_dep, last_url, last_req, last_resp = dep, req.url, req.json, None
            incr_inflight(dep.deployment_id)
            try:
                resp = await self.client.request(
                    req.method, req.url, headers=req.headers, json=req.json,
                    timeout=_settings.request_timeout,
                )
            except httpx.HTTPError as exc:
                circuit_breaker.record_failure(dep.deployment_id)
                last_status, last_body, last_resp = 502, f"connection error: {exc}", f"connection error: {exc}"
                continue
            finally:
                decr_inflight(dep.deployment_id)

            last_resp = resp.text
            if resp.status_code >= 400:
                retryable = resp.status_code == 429 or resp.status_code >= 500
                if retryable:
                    circuit_breaker.record_failure(dep.deployment_id)
                    last_status, last_body = resp.status_code, resp.text
                    continue
                # client error (4xx, non-429): surface immediately
                raise AllAttemptsFailed(
                    resp.status_code, resp.text, deployment=dep, upstream_url=req.url,
                    upstream_request=req.json, upstream_response=resp.text,
                )

            circuit_breaker.record_success(dep.deployment_id)
            data = resp.json()
            usage = adapter.extract_usage(data)
            await record_tokens(self.vk_id, dep, usage.total_tokens)
            return ChatResult(
                response=adapter.parse_chat_response(data),
                deployment=dep, usage=usage, retries=retries,
                upstream_url=req.url, upstream_request=req.json, upstream_response=data,
            )

        raise AllAttemptsFailed(
            last_status, last_body, deployment=last_dep, upstream_url=last_url,
            upstream_request=last_req, upstream_response=last_resp,
        )

    async def run_stream(self, aliases: list[ResolvedAlias], body: dict[str, Any]) -> StreamResult:
        """Open the upstream stream, falling back only until the first byte."""
        attempts = _iter_attempts(aliases)
        last_status, last_body = 502, "no upstream available"
        last_dep: Optional[ResolvedDeployment] = None
        last_url: Optional[str] = None
        last_req: Optional[dict[str, Any]] = None
        retries = 0
        for i, dep in enumerate(attempts):
            if i > 0:
                retries += 1
                await asyncio.sleep(_settings.retry_backoff_base * (2 ** (i - 1)))
            if not await _rate_limit_ok(self.vk_id, dep, self.vk_rpm, self.vk_tpm):
                last_status, last_body = 429, "rate limit exceeded"
                continue
            adapter = get_adapter(dep.provider_type)
            params = _prepare_params(dep, body)
            params["stream"] = True
            req = adapter.build_chat_request(
                base_url=dep.base_url, api_key=dep.api_key, org=dep.org,
                extra_headers=dep.extra_headers, upstream_model=dep.upstream_model, params=params,
            )
            last_dep, last_url, last_req = dep, req.url, req.json
            cm = self.client.stream(
                req.method, req.url, headers=req.headers, json=req.json,
                timeout=_settings.request_timeout,
            )
            try:
                resp = await cm.__aenter__()
            except httpx.HTTPError as exc:
                circuit_breaker.record_failure(dep.deployment_id)
                last_status, last_body = 502, f"connection error: {exc}"
                continue

            if resp.status_code >= 400:
                err_body = (await resp.aread()).decode("utf-8", "replace")
                await cm.__aexit__(None, None, None)
                if resp.status_code == 429 or resp.status_code >= 500:
                    circuit_breaker.record_failure(dep.deployment_id)
                    last_status, last_body = resp.status_code, err_body
                    continue
                raise AllAttemptsFailed(
                    resp.status_code, err_body, deployment=dep, upstream_url=req.url,
                    upstream_request=req.json, upstream_response=err_body,
                )

            circuit_breaker.record_success(dep.deployment_id)
            result = StreamResult(chunks=None, deployment=dep, retries=retries,  # type: ignore
                                  upstream_url=req.url, upstream_request=req.json)
            result.chunks = self._consume_stream(cm, resp, adapter, dep, result)
            return result

        raise AllAttemptsFailed(
            last_status, last_body, deployment=last_dep, upstream_url=last_url,
            upstream_request=last_req, upstream_response=last_body,
        )

    async def _consume_stream(self, cm, resp, adapter, dep, result: StreamResult):
        incr_inflight(dep.deployment_id)
        try:
            async for chunk in adapter.transform_stream(resp.aiter_lines()):
                usage = chunk.get("usage")
                if usage:
                    result.usage = Usage(
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                    )
                yield chunk
        finally:
            decr_inflight(dep.deployment_id)
            await cm.__aexit__(None, None, None)
            if result.usage.total_tokens:
                await record_tokens(self.vk_id, dep, result.usage.total_tokens)
