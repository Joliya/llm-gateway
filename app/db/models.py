from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Provider(Base):
    """An upstream LLM vendor / endpoint family."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Logical name used as a prefix, e.g. "openai", "kimi", "deepseek".
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # Adapter selector: openai_compat | anthropic | gemini
    provider_type: Mapped[str] = mapped_column(String(50))
    default_base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    credentials: Mapped[list["Credential"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class Credential(Base):
    """An API key (+ endpoint overrides) belonging to a provider."""

    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    api_key_enc: Mapped[str] = mapped_column(Text)            # Fernet-encrypted
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    org: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    extra_headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    rpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    provider: Mapped[Provider] = relationship(back_populates="credentials")
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="credential", cascade="all, delete-orphan"
    )


class Alias(Base):
    """Client-facing logical model name = a load-balancing group."""

    __tablename__ = "aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    lb_strategy: Mapped[str] = mapped_column(String(30), default="round_robin")
    # Ordered list of alias names to fall back to when this one is exhausted.
    fallback_aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    cache_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="alias", cascade="all, delete-orphan"
    )


class Deployment(Base):
    """A concrete callable model instance: (alias, credential, upstream model)."""

    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias_id: Mapped[int] = mapped_column(ForeignKey("aliases.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[int] = mapped_column(ForeignKey("credentials.id", ondelete="CASCADE"), index=True)
    upstream_model: Mapped[str] = mapped_column(String(200))
    weight: Mapped[int] = mapped_column(Integer, default=1)
    rpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Param management (see transform/params.py):
    pinned_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    drop_params: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Pricing per 1M tokens (USD or any unit; only used for cost accounting).
    input_price: Mapped[float] = mapped_column(Float, default=0.0)
    output_price: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    alias: Mapped[Alias] = relationship(back_populates="deployments")
    credential: Mapped[Credential] = relationship(back_populates="deployments")


class VirtualKey(Base):
    """A proxy API key issued to downstream callers."""

    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(20))      # for display, e.g. "sk-gw-ab12"
    name: Mapped[str] = mapped_column(String(150))
    # "*" (any) or explicit list of alias names this key may call.
    allowed_aliases: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["*"])
    rpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_period: Mapped[str] = mapped_column(String(20), default="total")  # total|daily|monthly
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    budget_anchor: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RequestLog(Base):
    """One row per proxied request, for usage / cost auditing."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    virtual_key_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    requested_model: Mapped[str] = mapped_column(String(200))
    alias: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    deployment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=200)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


__all__ = [
    "Provider",
    "Credential",
    "Alias",
    "Deployment",
    "VirtualKey",
    "RequestLog",
]
