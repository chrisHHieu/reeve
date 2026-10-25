"""Token storage and refresh for Anthropic/Claude subscription OAuth.

Stores tokens safely in the secret store profile (`provider:anthropic-subscription`),
supporting automatic refresh near expiry and clearing upon revocation.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional

from .constants import (
    client_id,
    EXPIRED_ERROR,
    PROFILE,
    REDIRECT_URI,
    REFRESH_MARGIN_SECONDS,
    SIGNED_OUT_ERROR,
    TOKEN_URL,
    AnthropicSubscriptionAuthError,
    AnthropicSubscriptionSignInRequired,
)

logger = logging.getLogger(__name__)


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _token_post(data: dict[str, str], timeout: float = 30.0) -> Any:
    """One POST to the Anthropic token endpoint."""
    import httpx

    return httpx.post(
        TOKEN_URL,
        json=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "axios/1.13.6",
        },
        timeout=timeout,
    )


def exchange_code(
    code: str,
    verifier: str,
    state: str = "",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exchange an authorization code and PKCE verifier for access and refresh tokens.

    Note: Anthropic's `/v1/oauth/token` strictly requires `state` in the POST body;
    omitting `state` causes HTTP 400 'Invalid request format'.
    """
    raw_code = code.strip()
    clean_code = raw_code.split("#", 1)[0]
    effective_state = state
    if "#" in raw_code and len(raw_code.split("#", 1)) > 1:
        fragment_state = raw_code.split("#", 1)[1]
        if fragment_state:
            effective_state = fragment_state

    payload: dict[str, Any] = {
        "grant_type": "authorization_code",
        "client_id": client_id(),
        "code": clean_code,
        "state": effective_state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }

    response = _token_post(payload, timeout=timeout)
    if response.status_code >= 300:
        logger.error(
            "Token exchange failed: status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AnthropicSubscriptionAuthError(
            f"Sign-in failed — token exchange returned HTTP {response.status_code}: {response.text}"
        )
    return response.json()


class AnthropicSubscriptionTokenStore:
    """Token store backed by the secret store under `provider:anthropic-subscription`."""

    def __init__(self, secrets: Any) -> None:
        self._secrets = secrets

    def _data(self) -> dict[str, Any]:
        return {} if self._secrets is None else self._secrets.get(PROFILE) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        if self._secrets is not None:
            self._secrets.put(PROFILE, {**self._data(), **patch})

    def signed_in(self) -> bool:
        tokens = self._data().get("tokens") or {}
        return bool(tokens.get("access_token") or tokens.get("refresh_token"))

    def account_label(self) -> Optional[str]:
        data = self._data()
        return data.get("account_email") or data.get("account_name") or data.get("account_id") or None

    def save(self, token_response: dict[str, Any]) -> None:
        existing = self._data().get("tokens") or {}
        access_token = token_response.get("access_token") or existing.get("access_token") or ""
        refresh_token = token_response.get("refresh_token") or existing.get("refresh_token") or ""
        expires_in = token_response.get("expires_in")
        
        now = time.time()
        expires_at = now + float(expires_in) if expires_in is not None else None

        merged = {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
        if expires_at is not None:
            merged["expires_at"] = expires_at

        patch: dict[str, Any] = {
            "tokens": merged,
            "tokens_issued_at": int(now),
        }

        # Inspect JWT claims if available for email/account info
        claims = jwt_claims(access_token)
        email = claims.get("email") or claims.get("preferred_username") or self._data().get("account_email")
        if email:
            patch["account_email"] = email

        self._merge(patch)

    def clear(self) -> bool:
        return False if self._secrets is None else bool(self._secrets.delete(PROFILE))

    def access_token(self) -> str:
        """Returns a live access token, refreshing if near expiry or expired."""
        data = self._data()
        tokens = data.get("tokens") or {}
        access = tokens.get("access_token") or ""
        refresh = tokens.get("refresh_token") or ""

        if not access and not refresh:
            raise AnthropicSubscriptionSignInRequired(SIGNED_OUT_ERROR)

        expires_at = tokens.get("expires_at")
        stale = False
        if not access:
            stale = True
        elif isinstance(expires_at, (int, float)):
            if expires_at - time.time() < REFRESH_MARGIN_SECONDS:
                stale = True
        else:
            # Check JWT exp claim if present
            claims = jwt_claims(access)
            exp = claims.get("exp")
            if isinstance(exp, (int, float)) and exp - time.time() < REFRESH_MARGIN_SECONDS:
                stale = True

        return self.refresh() if stale else access

    def refresh(self) -> str:
        """Exchange the refresh token for a new token set."""
        tokens = self._data().get("tokens") or {}
        refresh_val = tokens.get("refresh_token") or ""
        if not refresh_val:
            self.clear()
            raise AnthropicSubscriptionSignInRequired(EXPIRED_ERROR)

        client = client_id()
        try:
            response = _token_post(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_val,
                    "client_id": client,
                }
            )
        except Exception as exc:
            raise AnthropicSubscriptionAuthError(
                f"Couldn't reach Anthropic sign-in service to refresh session ({exc.__class__.__name__})."
            ) from exc

        if 400 <= response.status_code < 500:
            self.clear()
            raise AnthropicSubscriptionSignInRequired(EXPIRED_ERROR)
        if response.status_code >= 300:
            raise AnthropicSubscriptionAuthError(
                f"Claude subscription refresh failed (HTTP {response.status_code}) — try again."
            )

        self.save(response.json())
        data = self._data()
        return (data.get("tokens") or {}).get("access_token") or ""
