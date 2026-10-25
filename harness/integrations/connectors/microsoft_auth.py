"""Microsoft sign-in for the hand-written Microsoft 365 tools: storage, renewal, identity.

The same grant as Google's (`mcp/pkce_auth.py`: authorization code with PKCE, loopback redirect)
against the operator's Entra app, registered for "any organizational directory and personal
Microsoft accounts", so one client id serves outlook.com and every company tenant. It is a public
client: no secret is needed, and its id comes from `REEVE_MICROSOFT_CLIENT_ID` in the environment
or the state folder's `.env`, never from the source tree.

Before this the Outlook card asked for a pasted Graph access token, which lives about an hour and
was never renewed: connected in the morning, broken by lunch. A sign-in here carries
`offline_access`, so it comes with a refresh token, renewed by the clock before a call and once
more on a 401 (the token can end early on a password change or a revoked consent).

Tokens go into the connector's account rows (`<name>:account:<email>`), where the connector list
looks to decide the card is connected, the way `google_auth` does it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from harness.safety.secrets import SecretStore

logger = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
AUTHORIZE = f"{AUTHORITY}/authorize"
TOKEN = f"{AUTHORITY}/token"
GRAPH = "https://graph.microsoft.com/v1.0"
#: Asked with every connector's own scopes: a refresh token, and who signed in.
IDENTITY_SCOPES = ("offline_access", "openid", "profile", "User.Read")
#: Renew this long before the stated expiry, so a token cannot die mid-call.
_SKEW = 120
_TIMEOUT = 30.0


def with_identity(scope: str) -> str:
    parts = scope.split()
    for extra in IDENTITY_SCOPES:
        if extra not in parts:
            parts.append(extra)
    return " ".join(parts)


def account_email(access_token: str) -> str:
    """The signed-in address (a work account's mail, else its sign-in name), or ""."""
    import httpx

    try:
        response = httpx.get(
            f"{GRAPH}/me", params={"$select": "mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {access_token}"}, timeout=_TIMEOUT,
        )
        if response.status_code >= 400:
            return ""
        me = response.json()
        return str(me.get("mail") or me.get("userPrincipalName") or "")
    except Exception:
        return ""


def store_signin(secrets: SecretStore, connector: str, raw: dict[str, Any], *, client_id: str) -> dict[str, Any]:
    """Keep a finished sign-in as one account of `connector`, named by its email."""
    from . import accounts

    email = account_email(raw.get("access_token") or "")
    if not email:
        return {"ok": False, "error": "Microsoft did not say which account signed in"}
    body: dict[str, Any] = {
        "type": "oauth", "enabled": True, "managed": False, "account": email,
        "access_token": raw.get("access_token") or "", "ms_client_id": client_id,
    }
    if raw.get("refresh_token"):
        body["refresh_token"] = raw["refresh_token"]
    if raw.get("expires_in"):
        body["expires_at"] = int(time.time()) + int(raw["expires_in"])
    if raw.get("scope"):
        body["scope"] = raw["scope"]
    return accounts.add_account(secrets, connector, email, body)


def _refresh(secrets: SecretStore, key: str, profile: dict[str, Any]) -> Optional[str]:
    """A new access token from the stored refresh token, kept in place. Microsoft rotates the
    refresh token on every use, so the new one replaces the old."""
    import httpx

    refresh_token, client_id = profile.get("refresh_token"), profile.get("ms_client_id")
    if not (refresh_token and client_id):
        return None
    data = {"client_id": client_id, "refresh_token": refresh_token, "grant_type": "refresh_token"}
    if profile.get("scope"):
        data["scope"] = profile["scope"]
    try:
        response = httpx.post(TOKEN, data=data, timeout=_TIMEOUT)
        raw = response.json()
    except Exception as exc:
        logger.warning("microsoft token renewal failed for %s: %s", key, exc.__class__.__name__)
        return None
    if response.status_code >= 400 or not raw.get("access_token"):
        logger.warning("microsoft token renewal refused for %s: %s", key, raw.get("error") or response.status_code)
        return None
    updated = dict(profile)
    updated["access_token"] = raw["access_token"]
    if raw.get("expires_in"):
        updated["expires_at"] = int(time.time()) + int(raw["expires_in"])
    if raw.get("refresh_token"):
        updated["refresh_token"] = raw["refresh_token"]
    secrets.put(key, updated)
    return raw["access_token"]


def refreshed(secrets: SecretStore, connector: str, key: str, profile: dict[str, Any]) -> dict[str, Any]:
    """The profile with its access token renewed when it has aged out (only a browser sign-in
    carries `ms_client_id`; a pasted token is left alone), noted for renewal on a 401."""
    if not profile.get("ms_client_id"):
        return profile
    expires_at = profile.get("expires_at")
    if expires_at and time.time() >= int(expires_at) - _SKEW:
        if not _refresh(secrets, key, profile):
            logger.warning("%s sign-in could not be renewed; the call will ask to reconnect", connector)
    current = secrets.get(key) or profile
    note_issued(secrets, key, current)
    return current


# -- renewing after a 401 (see google_auth for why the clock is not enough) ------------------

_ISSUED: "OrderedDict[str, tuple[SecretStore, str]]" = OrderedDict()
_ISSUED_LIMIT = 64
_ledger_lock = threading.Lock()


def note_issued(secrets: SecretStore, key: str, profile: dict[str, Any]) -> None:
    token = (profile.get("access_token") or "").strip()
    if not token or not profile.get("ms_client_id"):
        return
    with _ledger_lock:
        _ISSUED[token] = (secrets, key)
        _ISSUED.move_to_end(token)
        while len(_ISSUED) > _ISSUED_LIMIT:
            _ISSUED.popitem(last=False)


def renew_after_401(refused_token: str) -> Optional[str]:
    """A new access token for the profile that issued `refused_token`, or None when this process
    did not issue it or Microsoft refused the renewal."""
    with _ledger_lock:
        source = _ISSUED.get(refused_token)
    if source is None:
        return None
    secrets, key = source
    profile = secrets.get(key) or {}
    if (profile.get("access_token") or "").strip() != refused_token:
        return (profile.get("access_token") or "").strip() or None
    renewed = _refresh(secrets, key, profile)
    if renewed:
        with _ledger_lock:
            _ISSUED.pop(refused_token, None)
            _ISSUED[renewed] = (secrets, key)
    return renewed
