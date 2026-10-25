"""Whether a web page lets another app show it in a frame — the Browser tab asks before it tries.

An iframe that a site refuses (`X-Frame-Options`, or a Content-Security-Policy `frame-ancestors`
that does not name everyone) shows a blank or an error page, and the frame cannot tell the app why:
the refusal is in the response's headers, which a cross-origin page never shows its embedder. So
the Browser tab asks here first, for an address outside this computer, and says "won't open inside
Reeve · Open in your browser" instead of leaving a blank pane.

The request is a GET through the same address guard as `web_fetch` (every hop checked and pinned,
loopback and private ranges refused), with no cookies, a short timeout, and the body never read:
only the headers are looked at. The person asked for the page; the site sees one more request for
it (NOTICE.md). A local address (a dev server) is not asked about: the GUI loads it directly.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

TIMEOUT_S = 4.0


def frame_refusal(headers: Any) -> str | None:
    """Why these response headers refuse a frame from another site, or None when they allow it."""
    xfo = str(headers.get("x-frame-options") or "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return "x-frame-options"
    for header in headers.get_list("content-security-policy") if hasattr(headers, "get_list") else [
        headers.get("content-security-policy") or ""
    ]:
        for directive in str(header).split(";"):
            parts = directive.strip().split()
            if parts and parts[0].lower() == "frame-ancestors" and "*" not in parts[1:]:
                return "frame-ancestors"
    return None


def frame_check(url: str) -> dict[str, Any]:
    """`{ok, frameable, reason?}` for an http(s) address; `{ok: False, error}` when it cannot say."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return {"ok": False, "error": "not a web address"}
    import httpx

    from .guard import get_checked

    client = httpx.Client(follow_redirects=False, timeout=TIMEOUT_S)
    try:
        resp = get_checked(client, parts.geturl(), stream=True)
        try:
            reason = frame_refusal(resp.headers)
        finally:
            resp.close()
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — an unreachable site is the frame's to show
        return {"ok": False, "error": type(exc).__name__}
    finally:
        client.close()
    if reason:
        return {"ok": True, "frameable": False, "reason": reason}
    return {"ok": True, "frameable": True}
