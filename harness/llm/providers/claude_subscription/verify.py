"""Verify Anthropic/Claude subscription credential by probing the Messages API."""

from __future__ import annotations

from typing import Any, Optional

from .constants import (
    API_BASE_URL,
    REQUIRED_BETAS,
    USER_AGENT,
    VERIFY_MODEL,
    AnthropicSubscriptionSignInRequired,
)
from .tokens import AnthropicSubscriptionTokenStore


def verify(secrets: Any, base_url: Optional[str] = None) -> dict[str, Any]:
    """Test the stored subscription token by making a minimal call to the Anthropic Messages API."""
    store = AnthropicSubscriptionTokenStore(secrets)
    if not store.signed_in():
        return {
            "ok": False,
            "error": "Not signed in — connect your Claude subscription in Settings ▸ Models.",
        }

    try:
        token = store.access_token()
    except AnthropicSubscriptionSignInRequired as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"Token retrieval failed: {exc}"}

    import httpx

    url = f"{base_url or API_BASE_URL}/v1/messages"
    headers = {
        "anthropic-version": "2023-06-01",
        "authorization": f"Bearer {token}",
        "anthropic-beta": REQUIRED_BETAS,
        "user-agent": USER_AGENT,
        "content-type": "application/json",
    }
    payload = {
        "model": VERIFY_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to reach Claude API: {exc}",
        }

    if response.status_code == 200:
        return {"ok": True, "account": store.account_label()}
    if response.status_code == 401:
        return {"ok": False, "error": "Claude subscription token rejected or expired (401)."}
    if response.status_code == 429:
        return {"ok": True, "warning": "Rate limit / usage limit reached, but credentials are valid."}
    return {
        "ok": False,
        "error": f"Claude API returned status {response.status_code}: {response.text[:200]}",
    }
