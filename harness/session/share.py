"""Session sharing to a person-configured endpoint — the honest `/share`.

Some tools share a session to their own cloud: `POST /session/:id/share`
returns a URL the app copies, and `unshare` revokes it
(`packages/app/src/pages/session/use-session-commands.tsx`, account/ShareNext
machinery). This project has NO sharing backend and will not pretend to: the person
configures an endpoint that accepts the exported session JSON, and only then does the
command do anything. `[share]` is user-global only (`harness/config/config.py`,
`_GLOBAL_ONLY_FIELDS`) — a cloned repository must never be able to redirect the upload
of your transcript.

The contract (also in docs/config.example.toml):

    [share]
    endpoint = "https://example.test/share"        # POST {"info": …, "messages": […]}
    # revoke_endpoint = "https://example.test/share/{share_id}"   # optional
    # revoke_method = "DELETE"                     # "DELETE" (default) | "POST"
    # token = "…"                                  # optional bearer token
    # timeout = 30.0                               # optional, seconds

The upload response is JSON; its URL is read from `url` / `share_url` / `link` and its
id from `share_id` / `id` / `shareId`. No URL → the upload failed even if the endpoint
said 200 (a link the person cannot copy is not a share).

`/unshare` exists only when `revoke_endpoint` is configured AND the upload returned an
id: without a way to revoke, the UI keeps the row disabled with that reason rather than
offering a button that cannot mean anything.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

DEFAULT_TIMEOUT = 30.0


def share_settings(config: Any) -> dict[str, Any]:
    """Normalize `[share]` out of a loaded Config; `{"endpoint": ""}` when unset.

    A scalar typo degrades to "not configured" (the config module's stance): sharing is
    never worth a session that cannot start.
    """
    raw = getattr(config, "share", None)
    if not isinstance(raw, dict):
        return {"endpoint": "", "revoke_endpoint": "", "revoke_method": "DELETE", "token": "", "timeout": DEFAULT_TIMEOUT}
    endpoint = str(raw.get("endpoint") or "").strip()
    revoke = str(raw.get("revoke_endpoint") or "").strip()
    method = str(raw.get("revoke_method") or "DELETE").strip().upper()
    if method not in ("DELETE", "POST"):
        method = "DELETE"
    try:
        timeout = max(1.0, float(raw.get("timeout") or DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "endpoint": endpoint,
        "revoke_endpoint": revoke,
        "revoke_method": method,
        "token": str(raw.get("token") or "").strip(),
        "timeout": timeout,
    }


def _headers(settings: dict[str, Any]) -> dict[str, str]:
    token = settings.get("token") or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _first(body: Any, *keys: str) -> str:
    if not isinstance(body, dict):
        return ""
    for key in keys:
        value = body.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def upload_session(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """POST the export to the configured endpoint; return `{ok, url, share_id}`.

    Errors are values, never exceptions: the route hands them to a toast, and a failed
    share must not read as a successful one.
    """
    endpoint = settings.get("endpoint") or ""
    if not endpoint:
        return {"ok": False, "error": "Sharing needs a configured [share] endpoint."}
    try:
        response = httpx.post(
            endpoint,
            json=payload,
            headers=_headers(settings),
            timeout=settings.get("timeout", DEFAULT_TIMEOUT),
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach the share endpoint: {exc}"}
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": f"The share endpoint refused the upload ({response.status_code}).",
        }
    try:
        body = response.json()
    except ValueError:
        body = None
    url = _first(body, "url", "share_url", "link")
    if not url:
        return {"ok": False, "error": "The share endpoint returned no URL."}
    return {
        "ok": True,
        "url": url,
        "share_id": _first(body, "share_id", "id", "shareId"),
        "shared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def revoke_session(settings: dict[str, Any], share: dict[str, Any]) -> dict[str, Any]:
    """DELETE/POST the revoke endpoint for a stored share; errors are values."""
    template = settings.get("revoke_endpoint") or ""
    if not template:
        return {"ok": False, "error": "The configured endpoint does not support revoke."}
    share_id = str(share.get("share_id") or "")
    if "{share_id}" in template:
        if not share_id:
            return {"ok": False, "error": "This share has no revocable id."}
        url = template.replace("{share_id}", urllib.parse.quote(share_id, safe=""))
    else:
        url = template
    method = settings.get("revoke_method", "DELETE")
    try:
        if method == "POST":
            body: Optional[dict[str, Any]] = {"share_id": share_id} if share_id else {}
            response = httpx.post(
                url,
                json=body,
                headers=_headers(settings),
                timeout=settings.get("timeout", DEFAULT_TIMEOUT),
            )
        else:
            response = httpx.delete(
                url,
                headers=_headers(settings),
                timeout=settings.get("timeout", DEFAULT_TIMEOUT),
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach the share endpoint: {exc}"}
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": f"The share endpoint refused the revoke ({response.status_code}).",
        }
    return {"ok": True}
