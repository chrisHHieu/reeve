"""Live model listing for the provider setup page ("Included models").

The curated matrix list is only the FALLBACK. When a provider is reachable, the provider's own API is
asked for the models the configured credential can see, so Settings shows what the account actually
serves and not a copy that drifts.

The request shapes follow the Test-button probes (`registry/verify/`): OpenAI-compatible
`GET {base}/models`, Anthropic `GET {base}/v1/models`, Gemini `GET {base}/v1beta/models?key=`,
Ollama's native `/api/tags`, Bedrock's `list_foundation_models()`, Vertex's regional publisher list,
and, for the ChatGPT Codex sign-in, the Codex CLI's own catalog. Nothing here raises: a failure
returns `{ok: False, error}` so the caller can fall back to the curated rows.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from . import clouds, shapes
from .fetch import MODELS_TIMEOUT, Listing

__all__ = ["CACHE_TTL", "MODELS_TIMEOUT", "clear_cache", "list_provider_models"]

#: A models list changes rarely, so a short TTL keeps the page quick without pinning stale data after
#: an edit to a key or endpoint (the credential's fingerprint is in the cache key, so an edit misses
#: the cache anyway).
CACHE_TTL = 300.0

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    """Drop every cached list (tests, and the manager after a key or endpoint edit)."""
    _cache.clear()


def _fingerprint(name: str, base_url: Optional[str], key: str) -> str:
    # The key never enters the cache itself, only a digest of it.
    digest = hashlib.sha1(f"{name}|{base_url or ''}|{key}".encode()).hexdigest()[:12]
    return f"{name}:{digest}"


def _failed(error: Optional[str]) -> dict[str, Any]:
    return {"ok": False, "models": [], "error": error}


def _outcome(listing: Listing) -> dict[str, Any]:
    models, error = listing
    return _failed(error) if models is None else {"ok": True, "models": models, "source": "live"}


def _default_root(descriptor: Any, base: Optional[str]) -> str:
    declared = next((f.default for f in descriptor.fields if f.key == "base_url" and f.default), "")
    return (base or declared or "https://api.openai.com/v1").rstrip("/")


def _fetch(name: str, key: str, base: Optional[str], fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    from ..registry.descriptors import BY_NAME
    from ..registry.verify import PROBE_ROOTS, probe_root

    descriptor = BY_NAME.get(name) or BY_NAME["openai"]
    if name in ("ark", "ark-agent-plan-cn"):
        # The Responses-compatible data plane documents no `/models` probe (see
        # `registry/verify/keys.py`), so there is nothing live to ask.
        return _failed("this provider exposes no model list endpoint")
    if name == "openai-codex":
        return _outcome(clouds.codex())
    if name == "anthropic-subscription":
        return _outcome(clouds.claude_subscription(fields, timeout))
    if name == "bedrock":
        return _outcome(clouds.bedrock(fields, timeout))
    if name == "vertex":
        return _outcome(clouds.vertex(fields, timeout))
    if name == "ollama":
        return _outcome(shapes.ollama(base or str(fields.get("base_url") or ""), timeout))
    if not key and descriptor.needs_key and name != "openrouter":
        return _failed("no API key stored — add one to list this provider's models")
    if name in dict(PROBE_ROOTS):
        root, bad = probe_root(base, name)
        if bad:
            return _failed(bad)
    else:
        root = _default_root(descriptor, base)
    return _outcome(shapes.by_shape(name, root, key, timeout))


def list_provider_models(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    timeout: float = MODELS_TIMEOUT,
    refresh: bool = False,
) -> dict[str, Any]:
    """The models one provider's stored credential can see, fetched live.

    Returns `{"ok", "models", "error"?}`, `models` being bare ids with no `provider:` prefix. It
    never raises, and `refresh=True` skips the cache and rewrites it.
    """
    key = (api_key or "").strip()
    base = (base_url or "").strip() or None
    cache_key = _fingerprint(name, base, key)
    if not refresh:
        hit = _cache.get(cache_key)
        if hit is not None and time.time() - hit[0] < CACHE_TTL:
            return dict(hit[1])
    result = _fetch(name, key, base, dict(fields or {}), timeout)
    if result.get("ok"):
        _cache[cache_key] = (time.time(), dict(result))
    return result
