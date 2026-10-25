"""The Test button for the subscription provider."""

from __future__ import annotations

import uuid
from typing import Any

from .constants import (
    CODEX_BASE_URL,
    EXPIRED_ERROR,
    PLAN_LIMIT_ERROR,
    SIGNED_OUT_ERROR,
    VERIFY_MODEL,
    CodexAuthError,
    CodexSignInRequired,
)
from .tokens import CodexTokenStore, backend_headers


def verify(secrets: Any, timeout: float = 10.0) -> dict[str, Any]:
    """One cheap authenticated request to the backend. It tells apart signed out (no tokens, or
    tokens rejected), expired (a 401 on a bearer that was thought live) and OK. It never
    raises; the result is `{ok, error?, state?}` like the other providers' verifies."""
    import httpx

    store = CodexTokenStore(secrets)
    if not store.signed_in():
        return {"ok": False, "error": SIGNED_OUT_ERROR, "state": "signed_out"}
    try:
        token, account = store.access_token()
    except CodexSignInRequired as exc:
        return {"ok": False, "error": str(exc), "state": "signed_out"}
    except CodexAuthError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        response = httpx.post(
            CODEX_BASE_URL + "/responses",
            headers={"Authorization": f"Bearer {token}", **backend_headers(account, str(uuid.uuid4()))},
            json={"model": VERIFY_MODEL, "input": "Reply with OK.", "store": False,
                  "stream": True, "max_output_tokens": 16},
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't reach the ChatGPT backend ({exc.__class__.__name__})."}
    if response.status_code < 300:
        return {"ok": True, "account": store.account_label()}
    if response.status_code in (401, 403):
        return {"ok": False, "error": EXPIRED_ERROR, "state": "expired"}
    if response.status_code == 429:
        # The sign-in is fine; the plan's usage window happens to be used up right now.
        return {"ok": True, "account": store.account_label(), "note": PLAN_LIMIT_ERROR}
    return {"ok": False, "error": f"The ChatGPT backend returned HTTP {response.status_code}."}
