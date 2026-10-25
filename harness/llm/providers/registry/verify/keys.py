"""The Test button for key-based providers: one cheap request that shows whether the key
works."""

from __future__ import annotations

from typing import Any, Optional

from ..descriptors import BY_NAME
from ..types import normalize_ollama_url
from .bedrock import verify_bedrock
from .vertex import verify_vertex

#: The root each probe starts from, for providers whose endpoint can be overridden. The API
#: version segment is added by the caller, so a proxy is entered as its root, the way the SDK
#: expects it, and not already ending in `/v1`.
PROBE_ROOTS = {
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}


def probe_root(raw: Optional[str], name: str) -> tuple[str, Optional[str]]:
    """(root, error) for a probe: the person's own endpoint, or the provider's default.

    The error is returned and not raised, because `verify_provider_key` promises never to
    raise. A malformed URL has to come back as a message the person can act on, and not as
    "Couldn't reach Claude (Anthropic) (ValueError)".
    """
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return PROBE_ROOTS[name], None
    if not url.startswith(("http://", "https://")):
        return "", f"Custom endpoint must be an http(s) URL (got {url!r})."
    return url, None


def _default_endpoint(descriptor: Any) -> str:
    return next((f.default for f in descriptor.fields if f.key == "base_url" and f.default), "")


def _probe(name: str, descriptor: Any, key: str, base_url: Optional[str], root: Optional[str], timeout: float):
    """The probe request for `name`, or a dict with an `error` when there is nothing to probe."""
    import httpx

    if name == "anthropic":
        # A custom endpoint is honoured here too. Otherwise the button reports "Invalid API
        # key" for a proxy that the request itself would have reached. The probe is
        # `<root>/v1/models`, the root the SDK appends `/v1/messages` to.
        return httpx.get(
            f"{root}/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )
    if name == "gemini":
        return httpx.get(f"{root}/v1beta/models", params={"key": key}, timeout=timeout)
    if name == "ollama":
        return httpx.get(normalize_ollama_url(base_url).rstrip("/") + "/models", timeout=timeout)
    if name in ("ark", "ark-agent-plan-cn"):
        from ...matrix import provider_model_ids

        base = (base_url or "").strip().rstrip("/") or _default_endpoint(descriptor).rstrip("/")
        # Ark documents no `/models` probe, so the test sends a one-token Responses request. The
        # probe model is the first curated one for the provider, the same list the Settings
        # fallback shows, and a provider with none cannot be tested this way.
        model = next(iter(provider_model_ids(name)), "")
        if not model:
            return {"error": f"No model is known for {descriptor.title}."}
        return httpx.post(
            base + "/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": "Reply with OK.", "max_output_tokens": 1, "store": False},
            timeout=timeout,
        )
    # OpenAI and every OpenAI-compatible endpoint (Azure, OpenRouter, the vendors, vLLM…)
    base = (
        (base_url or "").strip().rstrip("/")
        or _default_endpoint(descriptor).rstrip("/")
        or "https://api.openai.com/v1"
    )
    return httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)


def verify_provider_key(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Check a provider's credentials with one cheap call, usually listing models.

    The key is passed in directly so a person can test before saving. This never raises and
    returns `{ok, error?}`. The cloud providers with several fields (Bedrock, Vertex) take
    their whole form in `fields`; everything else uses `api_key` and `base_url`.
    """
    descriptor = BY_NAME.get(name) or BY_NAME["openai"]
    if descriptor.auth == "oauth":
        # An OAuth provider verifies from its stored tokens, which only the session manager can
        # reach (`SessionManager.verify_provider`).
        return {"ok": False, "error": f"{descriptor.title} verifies via its sign-in, not a key."}
    if name == "bedrock":
        return verify_bedrock(fields or {}, timeout)
    if name == "vertex":
        return verify_vertex(fields or {}, timeout)

    # Worked out before the request, so a malformed URL reports itself and does not come back
    # as "couldn't reach" from the connection handler below.
    root: Optional[str] = None
    if name in PROBE_ROOTS:
        root, bad = probe_root(base_url, name)
        if bad:
            return {"ok": False, "error": bad}
    try:
        response = _probe(name, descriptor, (api_key or "").strip(), base_url, root, timeout)
    except Exception as exc:  # DNS, connection, timeout: never let it reach the caller as a 500
        return {"ok": False, "error": f"Couldn't reach {descriptor.title} ({exc.__class__.__name__})."}
    if isinstance(response, dict):
        return {"ok": False, **response}

    if response.status_code < 300:
        return {"ok": True}
    if response.status_code in (401, 403):
        return {"ok": False, "error": "Server rejected the request." if name == "ollama" else "Invalid API key."}
    if response.status_code == 404 and name == "ollama":
        return {"ok": False, "error": "Reached the server, but no OpenAI-compatible /v1 API there."}
    return {"ok": False, "error": f"{descriptor.title} returned HTTP {response.status_code}."}
