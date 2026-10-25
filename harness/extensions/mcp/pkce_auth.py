"""Authorization-code + PKCE against a PRE-REGISTERED public client (RFC 7636 + 8252).

The third sign-in shape, for a vendor that supports neither of the first two:

  - `oauth.py`       — DCR. Nothing is registered up front; the client id is minted at
                       sign-in time. Atlassian, Notion, Postman, Upwork.
  - `device_auth.py` — RFC 8628. No DCR *and* a token endpoint that demands a secret, so
                       the browser redirect is impossible. GitHub.
  - here             — no DCR, but the token endpoint accepts a public/installed client,
                       so the redirect works if the client id is known in advance.

Google Workspace forced it (2026-09-06). `accounts.google.com` publishes no
`registration_endpoint`, so DCR cannot run, but it accepts installed ("Desktop app")
clients with a loopback redirect — a full one-click, with no code for the user to copy.
Google requires the client_secret on the exchange even so; its own documentation treats an
installed app's secret as non-confidential, and `gcloud` ships its pair in the open.

TRANSPORT-AGNOSTIC ON PURPOSE. This module hands back the vendor's raw token response and
stores nothing. Its only caller today is a connector with hand-written tools
(`connectors/google_auth.py`), because Google's MCP servers refuse `tools/call` outside
their Developer Preview Program. It stays here rather than moving under `connectors/`
because the same grant is what an MCP server without DCR would need: a vendor that
publishes one and advertises no `registration_endpoint` lands here rather than on the
DCR path in `oauth.py`.

It borrows `oauth.py`'s browser and loopback-callback machinery rather than opening a
second listener: the sidecar already routes `CALLBACK_PATH` into `deliver_callback`, and
that path enforces the `state` match keeping a stray local hit from resolving a live flow.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from .oauth import CALLBACK_PATH, PROFILE_PREFIX, redirect_base

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
class PkceAuthError(RuntimeError):
    """The vendor refused, or the user never finished the browser flow."""


def _pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge), both base64url without padding per RFC 7636."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def redirect_uri() -> str:
    return redirect_base() + CALLBACK_PATH


def build_authorize_url(
    *,
    auth_endpoint: str,
    client_id: str,
    scope: str,
    state: str,
    challenge: str,
    extra: Optional[dict[str, str]] = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(extra or {})
    return f"{auth_endpoint}?{urlencode(params)}"


async def _exchange(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
) -> dict[str, Any]:
    data = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
    }
    # Sent only when the vendor issued one. A true public client has none, and passing an
    # empty value is not the same as omitting the field — some servers reject it.
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            token_endpoint, data=data, headers={"Accept": "application/json"}
        )
    try:
        raw = resp.json()
    except Exception:
        raise PkceAuthError(f"token endpoint returned HTTP {resp.status_code}")
    if resp.status_code >= 400 or raw.get("error"):
        raise PkceAuthError(
            str(raw.get("error_description") or raw.get("error") or resp.status_code)
        )
    if not raw.get("access_token"):
        raise PkceAuthError("token endpoint returned no access token")
    return raw


async def authorize_raw(
    *,
    auth_endpoint: str,
    token_endpoint: str,
    client_id: str,
    client_secret: str = "",
    scope: str = "",
    extra_params: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """The browser half, returning the vendor's raw token response.

    Split out because not every caller stores tokens the same way: an MCP server reads
    them from `mcp-oauth:<name>`, while a connector with hand-written tools needs them in
    its OWN profile, where `setup.py` looks to decide whether the card is connected.
    """
    from .oauth.flow import FLOW

    verifier, challenge = _pkce_pair()
    state = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    url = build_authorize_url(
        auth_endpoint=auth_endpoint,
        client_id=client_id,
        scope=scope,
        state=state,
        challenge=challenge,
        extra=extra_params,
    )
    await FLOW.open_browser(url)
    code, _state = await FLOW.wait_for_callback()
    return await _exchange(
        token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        verifier=verifier,
    )
