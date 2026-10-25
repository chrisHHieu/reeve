"""The Google family: Gmail, Calendar, Docs, Sheets, Slides and Drive.

They are hand-written REST tools against `www.googleapis.com` and not Google's remote MCP servers.
Those servers exist and answer `initialize` and `tools/list` to an anonymous caller (57 tools across
the suite), but every `tools/call` is refused unless the Cloud project is enrolled in the Google
Workspace Developer Preview Program, a gate that cannot be seen from outside. The product APIs have
no such gate, so the tools are ours and work today. What was kept from that work is the browser
sign-in (`pkce_auth.py` and `google_auth.py` hold a refresh token), which replaced a pasted access
token that expired after an hour.

The scope each connector asks for is load-bearing. Each takes the narrowest scope that covers its
tools, which keeps the Cloud project in Google's *sensitive* tier or below. Widening any of them to
`drive` or `drive.readonly` would make the whole project RESTRICTED and require a paid annual
security assessment. Gmail therefore lives in a separate Cloud project, because the consent screen,
and with it the tier, is set per project.

The client ids and the readiness flag are read from `oauth_clients` when `build()` runs, and not
when this module is imported, so a test can change the environment and rebuild.
"""

from __future__ import annotations

from typing import Any

from ..descriptor import oauth_clients as clients
from ..descriptor.model import ConnectorDescriptor, Field
from ..descriptor.validators import validate_google_drive

_API = "https://www.googleapis.com/auth/"


def _browser_sign_in() -> dict[str, Any]:
    """The arguments every Google connector shares for the one-click browser sign-in."""
    return {
        "pkce_auth": True,
        "pkce_auth_endpoint": clients.GOOGLE_AUTH_URL,
        "oauth_token_endpoint": clients.GOOGLE_TOKEN_URL,
        "oauth_client_id": clients.GOOGLE_CLIENT_ID,
        "oauth_client_id_env": "GOOGLE_OAUTH_CLIENT_ID",
        "oauth_client_secret": clients.GOOGLE_CLIENT_SECRET,
        "oauth_client_secret_env": "GOOGLE_OAUTH_CLIENT_SECRET",
        "pkce_auth_extra": clients.GOOGLE_EXTRA,
    }


def _token_field(help_text: str) -> Field:
    return Field("access_token", "OAuth access token", secret=True, help=help_text)


def _file_tool(name: str, title: str, icon: str, blurb: str, color: str, aliases: tuple, scope: str, noun: str):
    """Docs, Sheets and Slides: one click only, limited to files opened or created with the agent."""
    return ConnectorDescriptor(
        name=name,
        title=title,
        icon=icon,
        blurb=blurb,
        auth="oauth",
        two_way=False,
        brand_color=color,
        logo=name,
        aliases=aliases,
        fields=[],
        instructions=[
            "One click signs in with Google in your browser.",
            f"The agent reaches {noun} you open or create with it, and not every file in your Drive.",
        ],
        available=clients.GOOGLE_READY,
        oauth_scope=f"{_API}{scope} {_API}drive.file openid email",
        **_browser_sign_in(),
    )


def build() -> list[ConnectorDescriptor]:
    gmail = ConnectorDescriptor(
        name="gmail",
        title="Gmail",
        icon="✉",
        blurb="Search, summarize, draft and send email.",
        auth="oauth",
        two_way=False,
        brand_color="#ea4335",
        aliases=("email", "mail", "google"),
        logo="gmail",
        fields=[_token_field("A Google OAuth token with Gmail scopes.")],
        instructions=[
            "One click signs in with Google in your browser.",
            "Manual: paste a Google OAuth access token with Gmail scopes. Google access tokens "
            "last about an hour, so one click is the practical way.",
        ],
        available=True,
        # A SEPARATE Cloud project from the other Workspace connectors, which is the whole point.
        # All 23 tools of Google's own Gmail MCP server refuse anything narrower than
        # `gmail.modify`, a RESTRICTED scope: a project holding it needs a paid annual security
        # assessment before it may serve anyone beyond its 100-name test list. The consent screen
        # is a per-project setting, so keeping Gmail beside the others would drag all five into
        # that assessment. Hence its own project and its own client id.
        pkce_auth=True,
        pkce_auth_endpoint=clients.GOOGLE_AUTH_URL,
        oauth_token_endpoint=clients.GOOGLE_TOKEN_URL,
        oauth_client_id=clients.GMAIL_CLIENT_ID,
        oauth_client_id_env="GMAIL_OAUTH_CLIENT_ID",
        oauth_client_secret=clients.GMAIL_CLIENT_SECRET,
        oauth_client_secret_env="GMAIL_OAUTH_CLIENT_SECRET",
        pkce_auth_extra=clients.GOOGLE_EXTRA,
        # `gmail.modify` covers all 23 mirrored tools. `gmail.send` is added because Google's MCP
        # server stops at drafts and has no send tool, while this connector already shipped one,
        # so dropping it would lose a capability. Unlike modify, send is only sensitive, and so
        # costs nothing in the tier.
        oauth_scope=f"{_API}gmail.modify {_API}gmail.send",
    )
    calendar = ConnectorDescriptor(
        name="google_calendar",
        title="Google Calendar",
        icon="◷",
        blurb="Check when you are free, summarize schedules and create events.",
        auth="oauth",
        two_way=False,
        brand_color="#4285f4",
        logo="google_calendar",
        fields=[_token_field("A Google OAuth token with Calendar scopes.")],
        instructions=[
            "One click signs in with Google in your browser and covers your calendars.",
            "Manual: paste a Google OAuth access token with Calendar scopes. Google access tokens "
            "last about an hour, so one click is the practical way and this is the fallback.",
        ],
        available=True,
        # `calendar` is the one scope that all 9 tools accept. It is sensitive, not restricted.
        oauth_scope=f"{_API}calendar openid email",
        **_browser_sign_in(),
    )
    docs = _file_tool(
        "google_docs",
        "Google Docs",
        "▤",
        "Read documents and edit what is in them.",
        "#4285f4",
        ("docs", "document", "google", "writing"),
        "documents",
        "documents",
    )
    sheets = _file_tool(
        "google_sheets",
        "Google Sheets",
        "▦",
        "Read and write cell ranges and formulas, and change a sheet's structure.",
        "#34a853",
        ("sheets", "spreadsheet", "google", "excel", "data"),
        "spreadsheets",
        "spreadsheets",
    )
    slides = _file_tool(
        "google_slides",
        "Google Slides",
        "◪",
        "Read how a presentation is built and edit slide content.",
        "#fbbc04",
        ("slides", "presentation", "google", "deck", "powerpoint"),
        "presentations",
        "presentations",
    )
    drive = ConnectorDescriptor(
        name="google_drive",
        title="Google Drive",
        icon="◬",
        blurb="Search, browse and read files in Google Drive.",
        auth="oauth",
        two_way=False,
        brand_color="#4285f4",
        logo="google_drive",
        fields=[_token_field("A Google OAuth token with a Drive read scope.")],
        instructions=[
            "One click signs in with Google in your browser.",
            "One click uses the per-file Drive scope, so the agent sees files you open or create "
            "with it and not your whole Drive. Searching the whole Drive needs a restricted scope "
            "that Google gates behind a paid security assessment.",
            "Manual: paste a Google OAuth access token with the Drive read-only scope.",
        ],
        validate=validate_google_drive,
        available=True,
        # `drive.file` is accepted by all 8 tools and is not even sensitive, so it needs no
        # verification. `drive` and `drive.readonly` reach the whole Drive and are RESTRICTED.
        # The instructions above say so, because the trade is visible to the person.
        oauth_scope=f"{_API}drive.file openid email",
        # Each connected account is keyed by its Google email, so several Drive accounts list the
        # way Gmail's do and not by the opaque `sub`.
        account_field="@identity",
        **_browser_sign_in(),
    )
    return [gmail, calendar, docs, sheets, slides, drive]
