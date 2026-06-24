from __future__ import annotations

from app.providers.base import UpstreamRequest
from app.providers.gemini import GeminiAdapter

# Express-mode global endpoint (API-key auth). Standard Vertex uses a regional
# host with a /projects/{id}/locations/{region}/ path and OAuth instead.
DEFAULT_BASE_URL = "https://aiplatform.googleapis.com/v1/publishers/google/models"


class VertexAdapter(GeminiAdapter):
    """Gemini on Vertex AI. Same generateContent body/streaming as AI Studio
    (reused from GeminiAdapter) — only the URL and auth differ.

    Two modes, distinguished by whether the configured base_url carries a
    ``/projects/`` path:

      * **Standard Vertex** (base_url has ``/projects/{id}/locations/{region}/
        publishers/google/models``): OAuth — ``Authorization: Bearer <api_key>``,
        where ``api_key`` must be a valid access token. Google rejects API keys
        here. This gateway has no SDK, so it cannot mint/refresh tokens; supply a
        live token (or front it with a token-refreshing proxy).
      * **Express mode** (global base_url, no ``/projects/``): API-key auth via
        ``?key=<api_key>`` — same simple flow as AI Studio.
    """

    provider_type = "vertex"

    def build_chat_request(self, *, base_url, api_key, org, extra_headers, upstream_model,
                           params, dialect=None):
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        stream = bool(params.get("stream"))
        verb = "streamGenerateContent" if stream else "generateContent"
        bearer = "/projects/" in base  # standard Vertex => OAuth access token
        query = ["alt=sse"] if stream else []
        if not bearer:
            query.append(f"key={api_key}")
        qs = ("?" + "&".join(query)) if query else ""
        url = f"{base}/{upstream_model}:{verb}{qs}"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(extra_headers or {})
        return UpstreamRequest(method="POST", url=url, headers=headers,
                               json=self._to_gemini_body(params))
