from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Provider ---
class ProviderCreate(BaseModel):
    name: str
    provider_type: str = Field(description="openai_compat | anthropic | gemini")
    default_base_url: Optional[str] = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    default_base_url: Optional[str] = None
    enabled: Optional[bool] = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    provider_type: str
    default_base_url: Optional[str]
    enabled: bool


# --- Credential ---
class CredentialCreate(BaseModel):
    provider_id: int
    name: str
    api_key: str
    base_url: Optional[str] = None
    org: Optional[str] = None
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    weight: int = 1
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    enabled: bool = True


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    org: Optional[str] = None
    extra_headers: Optional[dict[str, Any]] = None
    weight: Optional[int] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    enabled: Optional[bool] = None


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_id: int
    name: str
    base_url: Optional[str]
    org: Optional[str]
    extra_headers: dict[str, Any]
    weight: int
    rpm_limit: Optional[int]
    tpm_limit: Optional[int]
    enabled: bool
    # api key intentionally never returned


# --- Alias ---
class AliasCreate(BaseModel):
    name: str
    lb_strategy: str = "round_robin"
    fallback_aliases: list[str] = Field(default_factory=list)
    cache_enabled: Optional[bool] = None
    enabled: bool = True


class AliasUpdate(BaseModel):
    name: Optional[str] = None
    lb_strategy: Optional[str] = None
    fallback_aliases: Optional[list[str]] = None
    cache_enabled: Optional[bool] = None
    enabled: Optional[bool] = None


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    lb_strategy: str
    fallback_aliases: list[str]
    cache_enabled: Optional[bool]
    enabled: bool


# --- Deployment ---
class DeploymentCreate(BaseModel):
    alias_id: int
    credential_id: int
    upstream_model: str
    weight: int = 1
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    pinned_params: dict[str, Any] = Field(default_factory=dict)
    default_params: dict[str, Any] = Field(default_factory=dict)
    drop_params: list[str] = Field(default_factory=list)
    input_price: float = 0.0
    output_price: float = 0.0
    enabled: bool = True


class DeploymentUpdate(BaseModel):
    upstream_model: Optional[str] = None
    credential_id: Optional[int] = None
    weight: Optional[int] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    pinned_params: Optional[dict[str, Any]] = None
    default_params: Optional[dict[str, Any]] = None
    drop_params: Optional[list[str]] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    enabled: Optional[bool] = None


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alias_id: int
    credential_id: int
    upstream_model: str
    weight: int
    rpm_limit: Optional[int]
    tpm_limit: Optional[int]
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
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    max_budget: Optional[float] = None
    budget_period: str = "total"
    expires_at: Optional[dt.datetime] = None


class VirtualKeyUpdate(BaseModel):
    name: Optional[str] = None
    allowed_aliases: Optional[list[str]] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    max_budget: Optional[float] = None
    budget_period: Optional[str] = None
    enabled: Optional[bool] = None
    expires_at: Optional[dt.datetime] = None


class VirtualKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    key_prefix: str
    allowed_aliases: list[str]
    rpm_limit: Optional[int]
    tpm_limit: Optional[int]
    max_budget: Optional[float]
    budget_period: str
    spend: float
    enabled: bool
    expires_at: Optional[dt.datetime]


class VirtualKeyCreated(VirtualKeyOut):
    # Plaintext key, returned exactly once on creation.
    key: str
