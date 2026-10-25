"""Fixed values of the Anthropic/Claude subscription sign-in (OAuth with PKCE).

Follows the public client configuration established for Claude subscription flows (e.g. Claude Code
and OpenCode Anthropic Auth).
"""

from __future__ import annotations

# Endpoints
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
CONSOLE_AUTHORIZE_URL = "https://platform.claude.com/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
API_BASE_URL = "https://api.anthropic.com"

# Registered OAuth client ID for Claude Code subscription
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Loopback callback configuration
CALLBACK_PORT = 53692
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
MANUAL_CALLBACK_URL = "https://platform.claude.com/oauth/code/callback"

# Required OAuth scopes
SCOPES = [
    "org:create_api_key",
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
]
SCOPE = " ".join(SCOPES)

# Secret store profile name
PROFILE = "provider:anthropic-subscription"

FLOW_TIMEOUT_SECONDS = 300
REFRESH_MARGIN_SECONDS = 300

# Probe / verify model
VERIFY_MODEL = "claude-haiku-4-5"

# Required headers for Claude subscription / OAuth API wire
REQUIRED_BETAS = "oauth-2025-04-20,interleaved-thinking-2025-05-14"
CLAUDE_CODE_VERSION = "2.1.280"
USER_AGENT = f"claude-cli/{CLAUDE_CODE_VERSION} (external, cli)"

SIGNED_OUT_ERROR = (
    "Not signed in to Claude subscription — connect your account in Settings ▸ Models to use "
    "the subscription provider."
)
EXPIRED_ERROR = "Claude session expired — sign in again in Settings ▸ Models."
PORT_BUSY_ERROR = (
    f"Port {CALLBACK_PORT} is already in use — another Claude sign-in process may be running. "
    "Wait or quit it and start the sign-in again."
)


class AnthropicSubscriptionAuthError(RuntimeError):
    """A subscription-auth failure with a message the user can read."""


class AnthropicSubscriptionSignInRequired(AnthropicSubscriptionAuthError):
    """No usable tokens. An explicit sign-in is required."""
