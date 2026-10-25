"""A custom endpoint typed into Settings."""

from __future__ import annotations

from typing import Any, Optional


def normalize_base_url(raw: Any) -> Optional[str]:
    """The endpoint without a trailing slash, or None when nothing was entered.

    Anything that is not an http(s) URL is refused here by name: the SDK's own error for it
    says neither which field it came from nor where to fix it.
    """
    url = str(raw or "").strip().rstrip("/")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        raise RuntimeError(
            f"Custom endpoint must be an http(s) URL (got {url!r}) — fix it in "
            "Manage → Configure Models."
        )
    return url
