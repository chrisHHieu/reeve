"""The shared pieces of asking a provider for its model list."""

from __future__ import annotations

from typing import Any, Optional

MODELS_TIMEOUT = 10.0

#: What one fetch yields: the ids, or None with the reason it failed.
Listing = tuple[Optional[list[str]], Optional[str]]


def tidy(ids: Any) -> list[str]:
    """Unique, trimmed, non-empty ids in case-insensitive order."""
    return sorted({str(i).strip() for i in ids if str(i).strip()}, key=str.lower)


def get_json(
    url: str,
    *,
    headers: dict[str, str],
    params: Optional[dict[str, str]] = None,
    timeout: float = MODELS_TIMEOUT,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """One GET, as `(payload, None)` or `(None, why it failed)`."""
    import httpx

    try:
        response = httpx.get(url, headers=headers, params=params, timeout=timeout)
    except Exception as exc:  # DNS, connection or timeout
        return None, f"couldn't reach the provider ({exc.__class__.__name__})"
    if response.status_code >= 300:
        return None, f"the provider returned HTTP {response.status_code}"
    try:
        payload = response.json()
    except Exception:
        return None, "the provider answered with a non-JSON body"
    return payload if isinstance(payload, dict) else {}, None
