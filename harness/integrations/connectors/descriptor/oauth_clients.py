"""Where the connectors' OAuth clients come from: the operator's environment or `.env`, never the
source tree (`client_env`)."""

from __future__ import annotations

from harness.safety.secrets import client_env

from .model import Field


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
#: Both are needed or Google returns an access token and no refresh token, and the connector dies
#: an hour after sign-in. `prompt=consent` is required because Google leaves the refresh token out
#: of any re-authorization it considers already granted.
GOOGLE_EXTRA = (("access_type", "offline"), ("prompt", "consent"))

#: The Desktop-app client for the five Workspace servers other than Gmail. Empty in the source:
#: the operator's comes from `GOOGLE_OAUTH_CLIENT_ID`, and `_mcp_one_click_ready`
#: hides one-click until it is set, in place of a button that ends on Google's "invalid_client"
#: page.
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
#: Gmail's client belongs to a different Cloud project. It is kept apart so nobody wires Gmail to
#: the client above and quietly moves the other five connectors into the restricted tier that
#: needs a security assessment.
GMAIL_CLIENT_ID = ""
GMAIL_CLIENT_SECRET = ""
#: Docs, Sheets and Slides are MCP-only, with no token field to fall back on. Without a client id
#: their card would show an empty form with nothing to press, so they render as "soon", which is
#: the truth. Calendar and Drive stay available regardless: they still have a manual token path.
GOOGLE_READY = bool(GOOGLE_CLIENT_ID.strip() or client_env("GOOGLE_OAUTH_CLIENT_ID"))

ALLOWED_USERS_FIELD = Field(
    key="allowed_users",
    label="Allowed user IDs",
    required=False,
    help="Comma-separated IDs allowed to message the bot. Leave empty, then DM the bot and use Capture.",
    placeholder="123456789",
)
