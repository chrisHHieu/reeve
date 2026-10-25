"""Fixed values of the ChatGPT subscription sign-in, and its errors.

The sign-in uses OAuth 2.0 with PKCE against the vendor's auth service, with the public
subscription client id and the loopback redirect that id is registered for. The port is fixed:
any other fails the redirect-uri check on the server.
"""

from __future__ import annotations

AUTH_ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = AUTH_ISSUER + "/oauth/authorize"
TOKEN_URL = AUTH_ISSUER + "/oauth/token"
#: Where the subscription flow's public client id is read, in the environment or the state
#: folder's `.env`. None ships in the source tree, the way the connectors' one-click ids do not.
CLIENT_ID_ENV = "REEVE_CODEX_CLIENT_ID"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
#: Registered for that client id exactly as written; host and port are not ours to choose.
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "reeve"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
#: Where the tokens are stored: a secret-store profile, like every provider's credentials.
PROFILE = "provider:openai-codex"
FLOW_TIMEOUT_SECONDS = 300
#: A token this close to expiry is refreshed instead of being sent about to die.
REFRESH_MARGIN_SECONDS = 300
ACCOUNT_CLAIM = "https://api.openai.com/auth"
#: The smallest listed model, so the verify probe costs next to nothing.
VERIFY_MODEL = "gpt-5.1-codex-mini"

SIGNED_OUT_ERROR = (
    "Not signed in to ChatGPT — connect your account in Settings ▸ Models to use "
    "the subscription provider."
)
EXPIRED_ERROR = "ChatGPT session expired — sign in again in Settings ▸ Models."
PLAN_LIMIT_ERROR = (
    "ChatGPT plan limit reached — your subscription's rolling usage window (about "
    "5 hours) is used up. Wait for it to reset, upgrade the plan, or switch to an "
    "API-key provider."
)
PORT_BUSY_ERROR = (
    f"Port {CALLBACK_PORT} is already in use — the OpenAI Codex CLI is the usual "
    "holder. Quit it and start the sign-in again."
)

NO_CLIENT_ERROR = (
    f"ChatGPT sign-in needs an OAuth client id: set {CLIENT_ID_ENV} in Reeve's .env, or use an "
    "OpenAI API key."
)


class CodexAuthError(RuntimeError):
    """A subscription-auth failure with a message the person can read."""


class CodexSignInRequired(CodexAuthError):
    """No usable tokens. The fix is an explicit sign-in, never a browser opened silently."""


def client_id() -> str:
    """The configured client id; a readable error when there is none."""
    from harness.safety.secrets import client_env

    value = client_env(CLIENT_ID_ENV)
    if not value:
        raise CodexAuthError(NO_CLIENT_ERROR)
    return value
