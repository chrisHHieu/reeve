"""OAuth 2.0 Device Authorization Grant (RFC 8628) for remote MCP servers.

The escape hatch for vendors whose authorization server offers **no** Dynamic Client
Registration. `mcp/oauth.py` is DCR-only on purpose — nothing is registered up front, so
the sign-in is fully local with no broker to hold a client id. When a vendor will not
register a client for us, that flow cannot start at all, and the vendor's page says only
"we couldn't identify the app requesting access".

GitHub is the case that forced this (2026-09-05). It runs a real remote MCP server and
advertises PKCE, but:

  - it has no `registration_endpoint`, so DCR is out; and
  - its token endpoint **still requires a client secret even with PKCE** — GitHub does
    not distinguish public from confidential clients — so the authorization-code flow is
    out too for a desktop app, which cannot keep a secret.

Device flow is the one grant GitHub offers that needs no secret. The client id it does
need is a public identifier, read from the operator's environment or `.env`
rather than from a broker, and the desktop stays the only party holding anything.

The trade for losing DCR is one paste: the vendor mints a short `user_code`, the user
enters it at `verification_uri`, and we poll until they approve. GitHub returns no
`verification_uri_complete`, so the code cannot be pre-filled into the URL — the caller
copies it to the clipboard instead.

Tokens land in the SAME SecretStore profile shape `mcp/oauth.py` uses (`mcp-oauth:<name>`),
so `client.py` reads one place regardless of how the token was obtained, and mcp.json —
which is plain text and paste-shareable — never sees a credential.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from harness.safety.secrets import SecretStore
from .oauth import PROFILE_PREFIX

logger = logging.getLogger(__name__)

#: Give up if the user never finishes at the vendor's page. RFC 8628 lets the server
#: state its own `expires_in`; this is the ceiling we impose regardless.
FLOW_TIMEOUT_SECONDS = 300
#: Fallback poll interval when the server does not send one (RFC 8628 §3.2 default).
_DEFAULT_INTERVAL = 5.0
_TIMEOUT = 20.0


class DeviceFlowError(RuntimeError):
    """The vendor refused, or the user never finished."""


def _profile(name: str) -> str:
    return PROFILE_PREFIX + name


class DeviceCode:
    """What the user needs on screen, plus what we need to poll with."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.device_code: str = raw.get("device_code") or ""
        self.user_code: str = raw.get("user_code") or ""
        self.verification_uri: str = (
            raw.get("verification_uri") or raw.get("verification_url") or ""
        )
        # Some vendors pre-fill the code into a URL; GitHub does not. Kept because it
        # removes the paste entirely wherever it IS offered.
        self.verification_uri_complete: str = raw.get("verification_uri_complete") or ""
        self.interval: float = float(raw.get("interval") or _DEFAULT_INTERVAL)
        self.expires_in: int = int(raw.get("expires_in") or FLOW_TIMEOUT_SECONDS)

    @property
    def open_url(self) -> str:
        """Where to send the browser — the pre-filled variant when the vendor has one."""
        return self.verification_uri_complete or self.verification_uri

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "open_url": self.open_url,
            # True when the user must paste the code themselves. False means the URL
            # already carries it and the screen is informational.
            "needs_code_entry": not self.verification_uri_complete,
            "expires_in": self.expires_in,
        }


async def request_code(
    *, device_endpoint: str, client_id: str, scope: str = ""
) -> DeviceCode:
    """Step 1: ask the vendor for a user code. No secret is sent, by design."""
    data = {"client_id": client_id}
    if scope:
        data["scope"] = scope
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            device_endpoint, data=data, headers={"Accept": "application/json"}
        )
    if resp.status_code >= 400:
        raise DeviceFlowError(
            f"device authorization refused ({resp.status_code}): {resp.text[:200]}"
        )
    raw = resp.json()
    if raw.get("error"):
        raise DeviceFlowError(str(raw.get("error_description") or raw["error"]))
    code = DeviceCode(raw)
    if not code.device_code or not code.user_code:
        raise DeviceFlowError("device authorization returned no code")
    return code


async def poll_for_token(
    *, token_endpoint: str, client_id: str, code: DeviceCode
) -> dict[str, Any]:
    """Step 2: poll until the user approves, declines, or the code expires.

    RFC 8628 §3.5 defines the two non-fatal errors: `authorization_pending` means keep
    waiting, `slow_down` means keep waiting AND lengthen the interval. Treating either
    as failure is the classic bug — the user is still typing.
    """
    interval = code.interval
    deadline = time.monotonic() + min(code.expires_in, FLOW_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            resp = await client.post(
                token_endpoint,
                data={
                    "client_id": client_id,
                    "device_code": code.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            raw = resp.json() if "json" in resp.headers.get("content-type", "") else {}
            err = raw.get("error")
            if not err and raw.get("access_token"):
                return raw
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5.0
                continue
            raise DeviceFlowError(
                str(raw.get("error_description") or err or f"HTTP {resp.status_code}")
            )
    raise DeviceFlowError(
        f"sign-in timed out — the code was not entered in "
        f"{int(min(code.expires_in, FLOW_TIMEOUT_SECONDS) // 60)} minutes"
    )


def store_tokens(server_name: str, raw: dict[str, Any], secrets: SecretStore) -> None:
    """Persist in the shape `mcp/oauth.py` writes, so `client.py` reads one place.

    `tokens_issued_at` is what lets a later read tell a live token from an hour-old one —
    the same staleness guard the OAuth storage applies.
    """
    tokens = {
        k: raw[k]
        for k in ("access_token", "refresh_token", "token_type", "scope", "expires_in")
        if raw.get(k) is not None
    }
    tokens.setdefault("token_type", "bearer")
    profile = dict(secrets.get(_profile(server_name)) or {})
    profile.update({"tokens": tokens, "tokens_issued_at": int(time.time())})
    secrets.put(_profile(server_name), profile)


def bearer_header(server_name: str, secrets: SecretStore) -> Optional[dict[str, str]]:
    """The Authorization header for a device-flow server, or None when signed out.

    Read at CONNECT time and passed to the transport — never written into mcp.json,
    which is plain text and routinely pasted between machines.
    """
    tokens = (secrets.get(_profile(server_name)) or {}).get("tokens") or {}
    access = (tokens.get("access_token") or "").strip()
    if not access:
        return None
    kind = (tokens.get("token_type") or "Bearer").strip() or "Bearer"
    return {"Authorization": f"{kind.title()} {access}"}


def granted_scopes(server_name: str, secrets: SecretStore) -> Optional[set[str]]:
    """The scopes the stored token was granted; None when the token does not say.

    A pasted personal access token is stored without a `scope`, and its scopes are not
    knowable from here — None keeps "unknown" from reading as "granted nothing".
    """
    tokens = (secrets.get(_profile(server_name)) or {}).get("tokens") or {}
    if "scope" not in tokens:
        return None
    return {s for s in str(tokens.get("scope") or "").replace(",", " ").split() if s}


def has_tokens(server_name: str, secrets: SecretStore) -> bool:
    return bool((secrets.get(_profile(server_name)) or {}).get("tokens"))


def sign_out(server_name: str, secrets: SecretStore) -> bool:
    return secrets.delete(_profile(server_name))
