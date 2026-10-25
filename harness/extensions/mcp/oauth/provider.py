"""The httpx auth for one OAuth MCP server."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata

from harness.safety.secrets import SecretStore

from .flow import FLOW
from .profile import CLIENT_NAME, redirect_uri
from .registration import invalidate_stale_registration
from .storage import SecretStoreTokenStorage

logger = logging.getLogger(__name__)


class MetadataSeededProvider(OAuthClientProvider):
    """An `OAuthClientProvider` that keeps the authorization-server metadata it discovers and loads
    it again next time.

    Without this, the SDK's pre-request refresh grant runs before discovery and falls back to
    `<origin>/token`. On a vendor whose real endpoint lives elsewhere (data.acme.link uses
    `/api/auth/mcp/token`) that is a 404, and every silent refresh turned into a demand for a full
    sign-in (2026-08-21, together with the stale-expiry flaw in `storage.py`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._store: SecretStoreTokenStorage = kwargs.get("storage") or self.context.storage  # type: ignore[assignment]

    async def _initialize(self) -> None:
        await super()._initialize()
        cached = self._store._data().get("oauth_metadata")
        if cached and self.context.oauth_metadata is None:
            try:
                self.context.oauth_metadata = OAuthMetadata.model_validate(cached)
            except Exception:
                pass  # a stale or incompatible cache, and discovery will fill it again
        if self.context.oauth_metadata is None and self._store._data().get("tokens"):
            await self._fetch_well_known()

    async def _fetch_well_known(self) -> None:
        """One best-effort fetch from the standard well-known location, for tokens that predate the
        cache, so the refresh grant can aim at the real token endpoint on the very next request. It is
        cached on success, and any failure falls back to the SDK's own discovery after a 401."""
        try:
            import httpx

            parts = urlparse(self.context.server_url)
            url = f"{parts.scheme}://{parts.netloc}/.well-known/oauth-authorization-server"
            async with httpx.AsyncClient(timeout=10) as client:
                reply = await client.get(url, headers={"Accept": "application/json"})
            if reply.status_code == 200:
                self.context.oauth_metadata = OAuthMetadata.model_validate(reply.json())
                self._persist_metadata()
        except Exception:
            pass

    def _persist_metadata(self) -> None:
        metadata = self.context.oauth_metadata
        if metadata is None:
            return
        try:
            self._store._merge({"oauth_metadata": metadata.model_dump(mode="json", exclude_none=True)})
        except Exception:
            logger.debug("could not persist oauth metadata", exc_info=True)

    async def _handle_token_response(self, response: Any) -> None:
        await super()._handle_token_response(response)
        self._persist_metadata()

    async def _handle_refresh_response(self, response: Any) -> bool:
        accepted = await super()._handle_refresh_response(response)
        if accepted:
            self._persist_metadata()
        return accepted


def build_auth(
    server_name: str, server_url: str, secrets: SecretStore, *, interactive: bool = True
) -> OAuthClientProvider:
    """The httpx auth for one OAuth MCP server, to pass as `streamablehttp_client(auth=…)`.

    With `interactive=False` stored tokens and silent refresh still work, but the moment the SDK
    wants a browser authorization it raises `InteractiveAuthRequired` and does not open one. Only an
    explicit connect passes True."""
    invalidate_stale_registration(server_name, server_url, secrets)
    metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri()],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # A public client: DCR issues no secret that a native app could keep anyway.
            "token_endpoint_auth_method": "none",
        }
    )
    return MetadataSeededProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=SecretStoreTokenStorage(server_name, secrets, server_url),
        redirect_handler=FLOW.open_browser if interactive else FLOW.refuse_browser,
        callback_handler=FLOW.wait_for_callback if interactive else FLOW.refuse_callback,
    )
