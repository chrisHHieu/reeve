"""Browser OAuth for remote MCP servers: OAuth 2.1 with PKCE and Dynamic Client Registration.

The official SDK's `OAuthClientProvider` drives the whole spec flow (protected-resource metadata
discovery, DCR, PKCE, token refresh) as an httpx auth plugged into the streamable-HTTP transport.
Three integration points are ours:

- token persistence, in the secret store (`storage.py`)
- the redirect, which opens the system browser at the authorize URL (`flow.py`)
- the callback, where the sidecar's loopback `GET /mcp/oauth/callback` resolves a single pending
  future (`flow.py`)

DCR means no client id or secret is registered anywhere beforehand, so there is nothing for a broker
to hold and the flow is entirely local.
"""

from __future__ import annotations

from typing import Optional

from harness.safety.secrets import SecretStore

from .errors import InteractiveAuthRequired, is_auth_required, is_http_auth_error
from .flow import FLOW, FLOW_TIMEOUT_SECONDS
from .profile import CALLBACK_PATH, CLIENT_NAME, PROFILE_PREFIX, profile_name, redirect_base
from .provider import build_auth
from .registration import invalidate_stale_registration
from . import flow, storage
from .storage import SecretStoreTokenStorage

__all__ = [
    "CALLBACK_PATH",
    "CLIENT_NAME",
    "FLOW_TIMEOUT_SECONDS",
    "InteractiveAuthRequired",
    "PROFILE_PREFIX",
    "SecretStoreTokenStorage",
    "build_auth",
    "deliver_callback",
    "has_tokens",
    "invalidate_stale_registration",
    "is_auth_required",
    "is_http_auth_error",
    "last_authorize_url",
    "redirect_base",
    "sign_out",
]


def deliver_callback(code: str, state: Optional[str]) -> bool:
    """What the loopback route calls. See `SignInFlow.deliver`."""
    return FLOW.deliver(code, state)


def last_authorize_url() -> Optional[str]:
    """The authorize URL of the latest sign-in, for the GUI's "reopen sign-in page" link."""
    return FLOW.last_authorize_url


def has_tokens(server_name: str, secrets: SecretStore) -> bool:
    return bool((secrets.get(profile_name(server_name)) or {}).get("tokens"))


def sign_out(server_name: str, secrets: SecretStore) -> bool:
    """Forget the tokens and the DCR registration, so the next connect runs a fresh flow."""
    return secrets.delete(profile_name(server_name))
