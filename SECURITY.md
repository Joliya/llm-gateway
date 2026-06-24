# Security Policy

llm-gateway is a self-hosted proxy that holds **upstream provider API keys** and
issues **virtual keys** for callers. A vulnerability here can expose those
credentials or another tenant's traffic, so we take reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[Report a vulnerability](https://github.com/Joliya/llm-gateway/security/advisories/new)
form (Security → Advisories). If you can't use it, contact the maintainers
listed in the repository profile.

Please include:

- a description of the issue and its impact;
- the version / commit you tested against;
- steps to reproduce (a minimal request or config is ideal);
- any logs or proof-of-concept, with secrets redacted.

We aim to acknowledge a report within **3 business days** and to share a
remediation plan or timeline within **10 business days**. We'll keep you updated
through resolution and credit you in the release notes unless you'd rather stay
anonymous.

## Supported versions

This project is pre-1.0 and moves fast. Security fixes land on `main` and in the
latest tagged release; older tags are not patched. Run a recent version.

## Operator responsibilities

The gateway can only protect what it's configured to. When you deploy it:

- **Set strong secrets.** `GW_MASTER_KEY` grants full admin access and
  `GW_ENCRYPTION_KEY` decrypts every stored credential. Generate them randomly,
  store them in a real secret manager, and never commit them. `.env` is
  gitignored — keep it that way.
- **Rotate on exposure.** If `GW_ENCRYPTION_KEY` leaks, assume every stored
  provider key is compromised and rotate them upstream.
- **Terminate TLS** in front of the gateway (ingress / reverse proxy). The app
  speaks plain HTTP; never expose `/admin/*` or `/v1/*` directly over the
  internet without TLS.
- **Restrict the admin surface.** `/admin/*` and the web console should sit
  behind your network controls, not be publicly reachable.
- **Scope virtual keys.** Give each caller its own key with the narrowest model
  allow-list and budget that works, so a leaked key has limited blast radius.

## What's in scope

Issues in this codebase: authentication/authorization bypass, credential
leakage, encryption weaknesses, injection, SSRF via provider configuration, and
similar. Vulnerabilities in upstream providers or in your own deployment
configuration are out of scope — but if our defaults make a mistake easy, we
want to hear about it.
