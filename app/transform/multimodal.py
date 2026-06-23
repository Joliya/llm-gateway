"""Multimodal (vision) content normalization.

Clients send images the OpenAI way — an ``image_url`` content block whose
``url`` is either a remote ``http(s)://`` link or a ``data:`` URI. Providers
disagree on what they accept:

    OpenAI / Qwen / Volcengine   remote URL ✓   data URI ✓
    Kimi / Moonshot              remote URL ✗   data URI ✓   (must inline)
    Anthropic                    remote URL ✓ (url source) + data URI ✓
    Gemini                       remote URL ✗   inline bytes only (must inline)

For providers that can't fetch a remote URL themselves, the gateway downloads
it and rewrites the block as a base64 ``data:`` URI. Adapters then map the
canonical OpenAI blocks into each vendor's native shape.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config import get_settings
from app.transform.reasoning import detect_openai_dialect

_settings = get_settings()


class ImageFetchError(Exception):
    """A remote image could not be downloaded or was too large."""


def _image_url_value(block: dict[str, Any]) -> str | None:
    """Pull the URL string out of an ``image_url`` block (dict or str form)."""
    iu = block.get("image_url")
    if isinstance(iu, dict):
        url = iu.get("url")
        return url if isinstance(url, str) else None
    if isinstance(iu, str):
        return iu
    return None


def _is_remote(url: str | None) -> bool:
    return bool(url) and (url.startswith("http://") or url.startswith("https://"))


def _iter_image_blocks(messages: Any):
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                yield block


def has_remote_images(messages: Any) -> bool:
    """True if any message carries an ``image_url`` block with a remote URL."""
    return any(_is_remote(_image_url_value(b)) for b in _iter_image_blocks(messages))


def parse_data_uri(uri: str) -> tuple[str, str] | None:
    """Split ``data:<mime>;base64,<payload>`` into ``(mime, base64_payload)``.

    Returns ``None`` for anything that isn't a data URI we can use.
    """
    if not isinstance(uri, str) or not uri.startswith("data:"):
        return None
    header, sep, payload = uri.partition(",")
    if not sep or not payload:
        return None
    meta = header[len("data:"):]
    mime = meta.split(";")[0].strip() or "application/octet-stream"
    return mime, payload


def openai_content_to_anthropic(content: Any) -> Any:
    """Map an OpenAI message ``content`` to Anthropic's content shape.

    A plain string stays a string. A block list becomes Anthropic blocks:
    ``text`` blocks pass through; ``image_url`` blocks become an ``image`` block
    with a ``base64`` source (data URI) or a ``url`` source (remote link, which
    Anthropic fetches itself). Unknown blocks are dropped.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in (None, "text"):
            blocks.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image_url":
            url = _image_url_value(block)
            if not url:
                continue
            parsed = parse_data_uri(url)
            if parsed:
                mime, payload = parsed
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": payload}})
            elif _is_remote(url):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


def openai_content_to_gemini_parts(content: Any) -> list[dict[str, Any]]:
    """Map an OpenAI message ``content`` to Gemini ``parts``.

    Text becomes ``{"text": ...}``; ``image_url`` blocks become
    ``{"inlineData": {"mimeType", "data"}}``. Remote URLs are expected to have
    been inlined upstream (see :func:`normalize_images`); any that slip through
    are skipped since Gemini can't fetch them.
    """
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": "" if content is None else str(content)}]
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in (None, "text"):
            parts.append({"text": block.get("text", "")})
        elif btype == "image_url":
            parsed = parse_data_uri(_image_url_value(block) or "")
            if parsed:
                mime, payload = parsed
                parts.append({"inlineData": {"mimeType": mime, "data": payload}})
    return parts


async def _fetch_data_uri(client: httpx.AsyncClient, url: str, *, max_bytes: int,
                          timeout: float) -> str:
    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageFetchError(f"failed to fetch image {url}: {exc}") from exc
    data = resp.content
    if max_bytes and len(data) > max_bytes:
        raise ImageFetchError(f"image {url} is {len(data)} bytes (limit {max_bytes})")
    mime = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime or 'image/jpeg'};base64,{b64}"


async def inline_remote_images(
    client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    *,
    max_bytes: int,
    timeout: float,
    cache: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with remote ``image_url`` URLs replaced by
    base64 data URIs. Non-image content is shared, not deep-copied. ``cache``
    (keyed by source URL) avoids re-downloading the same image across fallback
    attempts within a single request."""
    cache = cache if cache is not None else {}
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list[Any] = []
        for block in content:
            url = _image_url_value(block) if (
                isinstance(block, dict) and block.get("type") == "image_url"
            ) else None
            if _is_remote(url):
                if url not in cache:
                    cache[url] = await _fetch_data_uri(
                        client, url, max_bytes=max_bytes, timeout=timeout
                    )
                iu = block.get("image_url")
                new_iu = {**iu, "url": cache[url]} if isinstance(iu, dict) else {"url": cache[url]}
                new_content.append({**block, "image_url": new_iu})
            else:
                new_content.append(block)
        out.append({**msg, "content": new_content})
    return out


def _needs_inlining(provider_type: str, base_url: str | None) -> bool:
    """Whether this provider needs remote image URLs converted to data URIs."""
    if provider_type == "gemini":
        return True  # Gemini only accepts inline image bytes
    if provider_type in ("openai", "openai_compat"):
        return detect_openai_dialect(base_url) == "kimi"  # Kimi: base64 only
    return False  # openai/qwen/volc accept URLs; anthropic handles them in-adapter


async def normalize_images(client: httpx.AsyncClient, dep: Any, params: dict[str, Any],
                           *, cache: dict[str, str] | None = None) -> None:
    """Rewrite ``params['messages']`` so ``dep``'s provider gets images it can
    ingest. No-op unless image fetching is enabled, the body carries a remote
    image, and the target provider can't fetch URLs itself. ``cache`` (shared
    across a request's fallback attempts) avoids duplicate downloads.

    Raises ``ImageFetchError`` if a required download fails.
    """
    if not _settings.image_fetch_enabled:
        return
    messages = params.get("messages")
    if not isinstance(messages, list) or not has_remote_images(messages):
        return
    if not _needs_inlining(dep.provider_type, dep.base_url):
        return
    params["messages"] = await inline_remote_images(
        client, messages,
        max_bytes=_settings.image_fetch_max_bytes,
        timeout=_settings.image_fetch_timeout,
        cache=cache,
    )
