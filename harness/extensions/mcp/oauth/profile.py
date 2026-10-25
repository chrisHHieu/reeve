"""Where a server's OAuth state is kept, and the redirect the sidecar listens on."""

from __future__ import annotations

import os

PROFILE_PREFIX = "mcp-oauth:"
CALLBACK_PATH = "/mcp/oauth/callback"
CLIENT_NAME = "Reeve"


def redirect_base() -> str:
    """The sidecar's own loopback origin. The redirect registered by DCR must match it."""
    return f"http://127.0.0.1:{os.environ.get('REEVE_PORT') or '8765'}"


def redirect_uri() -> str:
    return redirect_base() + CALLBACK_PATH


def profile_name(server_name: str) -> str:
    return PROFILE_PREFIX + server_name
