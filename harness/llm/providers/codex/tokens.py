"""The subscription's tokens: reading them, storing them, keeping them fresh.

The account id travels inside the token JWTs, in the `https://api.openai.com/auth` claim. They
are decoded without verifying the signature, because only routing claims are read here and the
backend is what verifies the token.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional

from .constants import (
    ACCOUNT_CLAIM,
    CLIENT_ID,
    EXPIRED_ERROR,
    ORIGINATOR,
    PROFILE,
    REDIRECT_URI,
    REFRESH_MARGIN_SECONDS,
    SIGNED_OUT_ERROR,
    TOKEN_URL,
    CodexAuthError,
    CodexSignInRequired,
)


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def account_id_from(tokens: dict[str, Any]) -> str:
    """The ChatGPT account id, from the auth claim of the id or access token."""
    for key in ("id_token", "access_token"):
        auth = jwt_claims(tokens.get(key) or "").get(ACCOUNT_CLAIM) or {}
        if isinstance(auth, dict):
            account = auth.get("chatgpt_account_id") or auth.get("account_id") or ""
            if account:
                return str(account)
    return ""


def backend_headers(account_id: str, session_id: str) -> dict[str, str]:
    """The headers other than auth that every backend request carries."""
    return {
        "chatgpt-account-id": account_id,
        "originator": ORIGINATOR,
        "OpenAI-Beta": "responses=experimental",
        "session-id": session_id,
    }


def _token_post(data: dict[str, str], timeout: float = 30.0) -> Any:
    """One POST to the token endpoint. Tests replace this to stand in for the network."""
    import httpx

    return httpx.post(TOKEN_URL, data=data, headers={"Accept": "application/json"}, timeout=timeout)


def exchange_code(code: str, verifier: str, timeout: float = 30.0) -> dict[str, Any]:
    """The token set for an authorization code and its PKCE verifier. Blocking: `sign_in` runs
    it in a thread."""
    response = _token_post(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        timeout,
    )
    if response.status_code >= 300:
        raise CodexAuthError(f"Sign-in failed — token exchange returned HTTP {response.status_code}.")
    return response.json()


class CodexTokenStore:
    """The token set and account details, in the `provider:openai-codex` secret-store profile.

    `access_token()` is what the provider calls on each request. It returns a live bearer,
    refreshing early when the token is close to its `exp`, and when the refresh token is
    rejected it clears the profile to a clean signed-out state instead of failing in a loop.
    """

    def __init__(self, secrets: Any) -> None:
        self._secrets = secrets

    def _data(self) -> dict[str, Any]:
        return {} if self._secrets is None else self._secrets.get(PROFILE) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        self._secrets.put(PROFILE, {**self._data(), **patch})

    def signed_in(self) -> bool:
        return bool(self._data().get("tokens"))

    def account_label(self) -> Optional[str]:
        data = self._data()
        return data.get("account_email") or data.get("account_id") or None

    def save(self, tokens: dict[str, Any]) -> None:
        """Store a token response. A refresh often returns no new refresh or id token, so what
        it leaves out is kept from before."""
        existing = self._data().get("tokens") or {}
        merged = {key: tokens.get(key) or existing.get(key)
                  for key in ("access_token", "refresh_token", "id_token")}
        merged = {key: value for key, value in merged.items() if value}
        patch: dict[str, Any] = {"tokens": merged, "tokens_issued_at": int(time.time())}
        account_id = account_id_from(merged) or self._data().get("account_id")
        if account_id:
            patch["account_id"] = account_id
        email = jwt_claims(merged.get("id_token") or "").get("email") or self._data().get("account_email")
        if email:
            patch["account_email"] = email
        self._merge(patch)

    def clear(self) -> bool:
        return False if self._secrets is None else bool(self._secrets.delete(PROFILE))

    def access_token(self) -> tuple[str, str]:
        """(a live access token, the account id), refreshing first when it is stale or absent."""
        data = self._data()
        tokens = data.get("tokens") or {}
        access = tokens.get("access_token") or ""
        if not access and not tokens.get("refresh_token"):
            raise CodexSignInRequired(SIGNED_OUT_ERROR)
        expires = jwt_claims(access).get("exp")
        stale = not access or (isinstance(expires, (int, float))
                               and expires - time.time() < REFRESH_MARGIN_SECONDS)
        return self.refresh() if stale else (access, data.get("account_id") or "")

    def refresh(self) -> tuple[str, str]:
        """Exchange the refresh token for a fresh (access token, account id). A rejected refresh
        token empties the profile, so the provider reads as cleanly signed out."""
        refresh = (self._data().get("tokens") or {}).get("refresh_token") or ""
        if not refresh:
            self.clear()
            raise CodexSignInRequired(EXPIRED_ERROR)
        try:
            response = _token_post(
                {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID}
            )
        except Exception as exc:
            raise CodexAuthError(
                "Couldn't reach the sign-in service to refresh the ChatGPT session "
                f"({exc.__class__.__name__})."
            ) from exc
        if 400 <= response.status_code < 500:
            self.clear()
            raise CodexSignInRequired(EXPIRED_ERROR)
        if response.status_code >= 300:
            raise CodexAuthError(f"ChatGPT session refresh failed (HTTP {response.status_code}) — try again.")
        self.save(response.json())
        data = self._data()
        return (data.get("tokens") or {}).get("access_token") or "", data.get("account_id") or ""
