"""The SDK's token storage, backed by the secret store.

One profile per server (`mcp-oauth:<server>`, a 0600 file) holds the token set and the client
registration DCR issued, which is reused across sign-ins. It is never `mcp.json`, which is plain text
and meant to be pasted around.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from harness.safety.secrets import SecretStore

from .profile import profile_name

# A token with less than this left is treated as already gone.
_EXPIRY_MARGIN_SECONDS = 60


class SecretStoreTokenStorage(TokenStorage):
    def __init__(self, server_name: str, secrets: SecretStore, server_url: str = "") -> None:
        self._name = server_name
        self._secrets = secrets
        self._server_url = server_url

    def _data(self) -> dict[str, Any]:
        return self._secrets.get(profile_name(self._name)) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        self._secrets.put(profile_name(self._name), {**self._data(), **patch})

    async def get_tokens(self) -> Optional[OAuthToken]:
        data = self._data()
        raw = data.get("tokens")
        if not raw:
            return None
        try:
            tokens = OAuthToken.model_validate(raw)
        except Exception:
            return None
        # A flaw in the SDK (mcp 1.29) is worked around here. `_initialize()` loads stored tokens but
        # never computes `token_expiry_time`, and `is_token_valid()` counts a missing expiry as valid
        # forever. So an hour-old access token is sent as it is, the server answers 401, and the
        # SDK's 401 path goes straight to a full re-authorization without trying the refresh token.
        # A non-interactive context must refuse the browser, which made every session say "sign-in
        # required" while an explicit connect appeared to work (2026-08-21, a Redshift server).
        #
        # The remedy sits in storage. When the stored token is past the lifetime recorded when it was
        # saved (an unknown age counts as stale), the token set comes back without its access token.
        # `is_token_valid()` then fails on its own terms and the SDK runs the refresh-token grant
        # first, which heals itself quietly with no browser.
        if tokens.expires_in is not None:
            issued = data.get("tokens_issued_at")
            left = int(issued + tokens.expires_in - time.time()) if isinstance(issued, (int, float)) else -1
            tokens = tokens.model_copy(update={"expires_in": left})
            if left <= _EXPIRY_MARGIN_SECONDS and tokens.refresh_token:
                tokens = tokens.model_copy(update={"access_token": ""})
        return tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._merge(
            {
                "tokens": tokens.model_dump(mode="json", exclude_none=True),
                "tokens_issued_at": int(time.time()),
            }
        )

    async def get_client_info(self) -> Optional[OAuthClientInformationFull]:
        raw = self._data().get("client_info")
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        # Record which server the registration belongs to. A DCR client id is issued by one
        # authorization server for one resource, and carrying it to another endpoint gets it refused
        # as unknown. The vendor's error page then says only "we couldn't identify the app", which
        # points nowhere near here. See `registration.invalidate_stale_registration`.
        patch: dict[str, Any] = {"client_info": info.model_dump(mode="json", exclude_none=True)}
        if self._server_url:
            patch["registered_for"] = self._server_url
        self._merge(patch)
