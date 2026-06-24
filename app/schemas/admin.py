from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- Provider ---
class ProviderCreate(BaseModel):
    name: str
    provider_type: str = Field(description="openai_compat | anthropic | gemini | vertex")
    default_base_url: str | None = None
    # Price book for prefix routing: {"gpt-4o": {"input": 2.5, "output": 10}}
    model_prices: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    default_base_url: str | None = None
    model_prices: dict[str, Any] | None = None
    enabled: bool | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    provider_type: str
    default_base_url: str | None
    model_prices: dict[str, Any]
    enabled: bool


# --- Credential ---
class CredentialCreate(BaseModel):
    provider_id: int
    name: str
    api_key: str
    base_url: str | None = None
    org: str | None = None
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    weight: int = 1
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    enabled: bool = True


class CredentialUpdate(BaseModel):
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    org: str | None = None
    extra_headers: dict[str, Any] | None = None
    weight: int | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    enabled: bool | None = None


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_id: int
    name: str
    base_url: str | None
    org: str | None
    extra_headers: dict[str, Any]
    weight: int
    rpm_limit: int | None
    tpm_limit: int | None
    enabled: bool
    # api key intentionally never returned


# --- Alias ---
class AliasCreate(BaseModel):
    name: str
    lb_strategy: str = "round_robin"
    fallback_aliases: list[str] = Field(default_factory=list)
    cache_enabled: bool | None = None
    enabled: bool = True


class AliasUpdate(BaseModel):
    name: str | None = None
    lb_strategy: str | None = None
    fallback_aliases: list[str] | None = None
    cache_enabled: bool | None = None
    enabled: bool | None = None


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    lb_strategy: str
    fallback_aliases: list[str]
    cache_enabled: bool | None
    enabled: bool


# --- Deployment ---
class DeploymentCreate(BaseModel):
    alias_id: int
    credential_id: int
    upstream_model: str
    # Explicit thinking dialect override (openai|qwen|deepseek|volc|kimi); set it
    # for aggregator endpoints whose base_url hides the real backend provider.
    dialect: str | None = None
    weight: int = 1
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    pinned_params: dict[str, Any] = Field(default_factory=dict)
    default_params: dict[str, Any] = Field(default_factory=dict)
    drop_params: list[str] = Field(default_factory=list)
    input_price: float = 0.0
    output_price: float = 0.0
    enabled: bool = True


class DeploymentUpdate(BaseModel):
    upstream_model: str | None = None
    credential_id: int | None = None
    dialect: str | None = None
    weight: int | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    pinned_params: dict[str, Any] | None = None
    default_params: dict[str, Any] | None = None
    drop_params: list[str] | None = None
    input_price: float | None = None
    output_price: float | None = None
    enabled: bool | None = None


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alias_id: int
    credential_id: int
    upstream_model: str
    dialect: str | None
    weight: int
    rpm_limit: int | None
    tpm_limit: int | None
    pinned_params: dict[str, Any]
    default_params: dict[str, Any]
    drop_params: list[str]
    input_price: float
    output_price: float
    enabled: bool


# --- Virtual key ---
class VirtualKeyCreate(BaseModel):
    name: str
    allowed_aliases: list[str] = Field(default_factory=lambda: ["*"])
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    max_budget: float | None = None
    budget_period: str = "total"
    expires_at: dt.datetime | None = None


class VirtualKeyUpdate(BaseModel):
    name: str | None = None
    allowed_aliases: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    max_budget: float | None = None
    budget_period: str | None = None
    enabled: bool | None = None
    expires_at: dt.datetime | None = None


class VirtualKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    key_prefix: str
    allowed_aliases: list[str]
    rpm_limit: int | None
    tpm_limit: int | None
    max_budget: float | None
    budget_period: str
    spend: float
    enabled: bool
    expires_at: dt.datetime | None


class VirtualKeyCreated(VirtualKeyOut):
    # Plaintext key, returned exactly once on creation.
    key: str


# --- Console users ---
class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=150)


class UserUpdate(BaseModel):
    enabled: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    enabled: bool
    created_at: dt.datetime
    last_login_at: dt.datetime | None


class UserCreated(UserOut):
    # Auto-generated password, returned exactly once (on create or reset).
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: dt.datetime
    username: str
