"""Google sign-in for the hand-written Workspace tools: storage, refresh, identity.

Google's own remote MCP servers exist and work — but every `tools/call` against them is
gated on the Google Workspace Developer Preview Program (verified 2026-09-06: the servers
answer `initialize` and `tools/list` to an anonymous caller, and only a real token on a
real Cloud project reveals the gate). The ordinary product APIs are NOT gated, so these
connectors call `www.googleapis.com` directly and keep the browser sign-in.

That sign-in is `mcp/pkce_auth.py` — authorization-code + PKCE against a shipped Desktop
client — and it is worth keeping for a reason unrelated to MCP: the connectors previously
asked the user to PASTE a Google access token, which expires in one hour. This module is
what makes that unnecessary.

WHERE TOKENS LIVE is the load-bearing decision here. They go into each connector's normal
profile — `google_calendar:account:<email>` for the account-shaped ones, `<name>:default`
for the rest — and NOT into the `mcp-oauth:` profile the sign-in would otherwise use. The
reason is a bug we already shipped once: `connectors/setup.py` derives `connected` for an
account-shaped connector from its account rows, so a sign-in that writes tokens anywhere
else leaves the card reading "not connected" while holding a perfectly good token, and the
connector is then cut from every session. Writing where the reader looks is the fix.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from harness.safety.secrets import SecretStore

logger = logging.getLogger(__name__)

#: Refresh this far before the stated expiry, so a token cannot die mid-call.
_SKEW = 120
_TIMEOUT = 30.0

#: Connectors whose profile is an account row rather than `<name>:default`.
#: `gmail` and `google_calendar` have bespoke accounts modules predating the generic one.
_GCAL = "google_calendar"
_GMAIL = "gmail"
_GENERIC_ACCOUNT = {"google_drive"}


def _identity_scope(scope: str) -> str:
    """Append the identity scopes, unless already present.

    Needed only to NAME the connected account. Both are non-sensitive, so they cost
    nothing in Google's verification tiers — and without an email the account-shaped
    connectors have no key to file the profile under.
    """
    parts = scope.split()
    for extra in ("openid", "email"):
        if extra not in parts:
            parts.append(extra)
    return " ".join(parts)


def account_email(access_token: str) -> str:
    """The signed-in address, or "" — used as the account key."""
    import httpx

    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            return ""
        return str(resp.json().get("email") or "")
    except Exception:
        return ""


def _profile_body(raw: dict[str, Any], *, client_id: str, client_secret: str) -> dict[str, Any]:
    """The stored shape. `expires_at` is absolute so a later read needs no clock math,
    and the client pair rides along so a refresh needs nothing but this profile."""
    body: dict[str, Any] = {
        "access_token": raw.get("access_token") or "",
        "type": "oauth",
        "enabled": True,
        "google_client_id": client_id,
        "google_client_secret": client_secret,
    }
    if raw.get("refresh_token"):
        body["refresh_token"] = raw["refresh_token"]
    if raw.get("expires_in"):
        body["expires_at"] = int(time.time()) + int(raw["expires_in"])
    if raw.get("scope"):
        body["scope"] = raw["scope"]
    return body


def store_signin(
    secrets: SecretStore,
    connector: str,
    raw: dict[str, Any],
    *,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Persist a completed sign-in where this connector's readers look."""
    email = account_email(raw.get("access_token") or "")
    body = _profile_body(raw, client_id=client_id, client_secret=client_secret)
    if email:
        body["account"] = email

    if connector == _GCAL:
        if not email:
            return {"ok": False, "error": "could not read the Google account email"}
        from . import gcal_accounts

        return gcal_accounts.managed_connect_account(secrets, {**body, "managed": False})

    if connector == _GMAIL:
        if not email:
            return {"ok": False, "error": "could not read the Google account email"}
        from . import gmail_accounts

        return gmail_accounts.managed_connect_account(secrets, {**body, "managed": False})

    if connector in _GENERIC_ACCOUNT:
        if not email:
            return {"ok": False, "error": "could not read the Google account email"}
        from . import accounts as _accounts

        return _accounts.add_account(
            secrets, connector, email, {**body, "managed": False}
        )

    previous = secrets.get(f"{connector}:default") or {}
    secrets.put(f"{connector}:default", {**previous, **body})
    return {"ok": True, "account": email or None}


def _refresh(secrets: SecretStore, key: str, profile: dict[str, Any]) -> Optional[str]:
    """Exchange the stored refresh token for a new access token, in place."""
    import httpx

    refresh_token = profile.get("refresh_token")
    client_id = profile.get("google_client_id")
    client_secret = profile.get("google_client_secret")
    if not (refresh_token and client_id):
        return None
    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        resp = httpx.post(
            "https://oauth2.googleapis.com/token", data=data, timeout=_TIMEOUT
        )
        raw = resp.json()
    except Exception as exc:
        logger.warning("google token refresh failed for %s: %s", key, exc)
        return None
    if resp.status_code >= 400 or not raw.get("access_token"):
        logger.warning(
            "google token refresh refused for %s: %s",
            key,
            raw.get("error_description") or raw.get("error") or resp.status_code,
        )
        return None
    updated = dict(profile)
    updated["access_token"] = raw["access_token"]
    if raw.get("expires_in"):
        updated["expires_at"] = int(time.time()) + int(raw["expires_in"])
    # A Google refresh response carries no refresh_token. Overwriting blindly would drop
    # the only way back and end the connection at the following expiry.
    if raw.get("refresh_token"):
        updated["refresh_token"] = raw["refresh_token"]
    secrets.put(key, updated)
    return raw["access_token"]


def fresh_token(
    secrets: SecretStore, connector: str, profile_key: str, profile: dict[str, Any]
) -> tuple[str, Optional[dict[str, str]]]:
    """(access_token, err) — refreshed if it has aged out.

    A profile with no `expires_at` is a hand-pasted token from the old flow: it is used
    as-is and will simply fail when it dies, which is the behaviour that path always had.
    """
    token = (profile.get("access_token") or "").strip()
    expires_at = profile.get("expires_at")
    if token and expires_at and time.time() >= int(expires_at) - _SKEW:
        renewed = _refresh(secrets, profile_key, profile)
        if renewed:
            return renewed, None
        if profile.get("refresh_token"):
            return "", {
                "error": (
                    f"{connector} sign-in expired and could not be renewed — "
                    "connect it again from its page"
                )
            }
    if not token:
        return "", {"error": f"{connector} is not connected"}
    return token, None


# -- renewing after a 401 ---------------------------------------------------------------
#
# Renewing by the clock is not enough. Google ends a token earlier than its stated expiry when
# the person revokes access, changes their password, or the refresh token is rotated, and a
# stored `expires_at` cannot know. The first sign of it is a 401 from the API. The request
# layer (`calls.request`) is the one door every Google call goes through, so it
# asks here what to do with a 401: the access token that was just refused is looked up in a
# small in-memory ledger of the tokens this process handed out, the profile it came from is
# renewed once, and the call is sent again with the new token.

_ISSUED: "OrderedDict[str, tuple[SecretStore, str]]" = OrderedDict()
_ISSUED_LIMIT = 64
_ledger_lock = threading.Lock()


def note_issued(secrets: SecretStore, key: str, profile: dict[str, Any]) -> None:
    """Remember which stored profile an access token came from, so a 401 on it can be
    renewed. Only browser sign-in profiles can be renewed; a pasted token has no client id."""
    token = (profile.get("access_token") or "").strip()
    if not token or not profile.get("google_client_id"):
        return
    with _ledger_lock:
        _ISSUED[token] = (secrets, key)
        _ISSUED.move_to_end(token)
        while len(_ISSUED) > _ISSUED_LIMIT:
            _ISSUED.popitem(last=False)


def renew_after_401(refused_token: str) -> Optional[str]:
    """A new access token for the profile that issued `refused_token`, or None when this
    process did not issue it or the refresh was refused."""
    with _ledger_lock:
        source = _ISSUED.get(refused_token)
    if source is None:
        return None
    secrets, key = source
    profile = secrets.get(key) or {}
    if (profile.get("access_token") or "").strip() != refused_token:
        # Another call already renewed this profile: use its token and refresh nothing.
        return (profile.get("access_token") or "").strip() or None
    renewed = _refresh(secrets, key, profile)
    if renewed:
        with _ledger_lock:
            _ISSUED.pop(refused_token, None)
            _ISSUED[renewed] = (secrets, key)
    return renewed
