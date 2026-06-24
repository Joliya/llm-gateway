# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-24

Initial public release — a lightweight, self-hosted, OpenAI-compatible LLM proxy.

### Added

- **OpenAI-compatible API**: `/v1/chat/completions`, `/v1/completions`,
  `/v1/embeddings`, `/v1/responses`, `/v1/images/generations`,
  `/v1/audio/*`, and `/v1/models`.
- **Multi-provider routing**: providers, credentials, aliases, and deployments
  managed from the database, with load balancing, fallback chains, circuit
  breaking, and per-deployment parameter pinning.
- **Cross-provider transforms**: thinking/reasoning dialects (OpenAI,
  Volcengine, OpenRouter, Vertex) and multimodal image inlining.
- **Virtual keys**: per-key model allow-lists, RPM/TPM limits, and daily/monthly
  budgets that reset automatically. Provider keys are encrypted at rest (Fernet).
- **Web console**: a self-contained static admin UI for providers, credentials,
  aliases, deployments, keys, users, traffic, and analytics, plus a Playground
  that exercises the real routing path. Client-side search on every config list.
- **Settings + currency rates**: a settings page with USD-based exchange rates
  that refresh daily from free public APIs (exchangerate-api primary, with
  fallbacks); monetary amounts render in a chosen display currency with
  full-precision hover.
- **Request log filtering**: filter traffic by model/alias (fuzzy), provider,
  and time range, with a default time window to keep queries index-bounded.
- **Observability**: per-request usage/cost logging, resolved provider/credential
  recording, optional upstream I/O capture, and a LiteLLM-style cost header.
- **Operations**: Docker image with Alembic migrations on startup, Postgres +
  Redis support, Kubernetes manifests, and multi-arch images published to GHCR.

[0.1.0]: https://github.com/Joliya/llm-gateway/releases/tag/v0.1.0
