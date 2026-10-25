"""What a connector is, as data.

Adding a connector is mostly data and not interface code: a descriptor declares how it signs in,
the fields a person fills in, the steps to follow, and a `validate` that confirms the credentials
with a real API call and returns the identity to show back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Field:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
        }


@dataclass
class MCPVariant:
    """One of several endpoints that the same vendor MCP server offers.

    Postman is the first case: `/minimal`, `/mcp` and `/code` are three tool sets behind three
    URLs, and which one a person wants depends on what they came to do. We cannot choose that for
    them, and the vendor already curates the sets.

    This is a choice of endpoint and not a filter on tools. Narrowing a vendor's catalogue to a
    list we keep is the design this codebase retired: the list drifts, names vanish without a
    sound, and the list bounds how fast we keep up and not what the person can do. Picking among
    the vendor's own endpoints has neither problem, because they keep the sets.

    Each variant is usually a different OAuth resource, so changing one means connecting again.
    """

    key: str
    label: str
    url: str
    summary: str  # what the person is choosing between, in one line

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "summary": self.summary}


@dataclass
class ValidationResult:
    ok: bool
    identity: Optional[str] = None  # what is shown back, such as "@mybot"; never a secret
    error: Optional[str] = None


@dataclass
class ConnectorDescriptor:
    name: str
    title: str
    icon: str
    blurb: str
    auth: str  # "bot_token", "socket_app", "oauth", "token", "api_token" or "none"
    two_way: bool
    fields: list[Field]
    instructions: list[str]
    available: bool = True  # False shows it as "soon"
    #: Sessions can subscribe to this connector's channels (Sources, Channels). Narrower than
    #: `two_way`: GitHub is two-way through the relay but has no channel semantics.
    channels: bool = False
    validate: Optional[Callable[[dict], ValidationResult]] = None
    #: Brand colour (hex, gray by default) and a stable logo id the app maps to a bundled SVG;
    #: an empty logo means the app's fallback.
    brand_color: str = "#6b7280"
    logo: str = ""
    #: Extra search terms for the catalogue's typeahead, words the title does not carry ("calendar"
    #: has to find Outlook, not only Google Calendar).
    aliases: tuple = ()

    # -- vendor-hosted MCP ------------------------------------------------------------------

    #: A vendor's MCP server URL makes the connector MCP-backed: one click runs the local MCP
    #: OAuth flow (dynamic client registration, tokens kept on this computer, no broker), and its
    #: tools are those the server reports. A connector can carry both `mcp_url` and manual fields
    #: (Jira), and the profile's `mode` says which tool set is live.
    mcp_url: str = ""
    #: A vendor with several endpoints of the same server (see `MCPVariant`). `mcp_url` is then
    #: the default variant's URL, so `bool(d.mcp_url)` keeps meaning "is this connector
    #: MCP-backed", and `mcp_url_for(d, profile)` resolves the endpoint actually in use.
    mcp_variants: tuple = ()
    mcp_variant_default: str = ""
    #: The device grant instead of dynamic registration, for a vendor whose authorization server
    #: registers no clients and demands a secret a desktop app cannot keep. It needs a public
    #: client id, which identifies the app and is not a credential.
    mcp_device_auth: bool = False
    mcp_device_endpoint: str = ""
    oauth_token_endpoint: str = ""
    oauth_scope: str = ""
    #: A client id carried by the descriptor itself. The source tree sets none: a
    #: literal there is in every clone and every secret scan of it.
    oauth_client_id: str = ""
    #: Where the operator's client id is read, in the environment or the state folder's `.env`
    #: (`client_env`). One-click sign-in is offered once it is set.
    oauth_client_id_env: str = ""
    #: Browser authorization code with PKCE against a pre-registered public client: for a vendor
    #: with no dynamic registration that does accept an installed client. The redirect works and
    #: nothing is copied by hand, unlike the device grant. It needs `pkce_auth_endpoint`,
    #: `oauth_token_endpoint`, `oauth_scope` and a client id.
    pkce_auth: bool = False
    pkce_auth_endpoint: str = ""
    #: Issued to installed (Desktop) clients by vendors that do not tell public from confidential
    #: ones. Google's own documentation calls it non-confidential. It stays out of `mcp.json`,
    #: which is plain text and gets pasted between machines. Empty is a true public client, and
    #: the field is left out.
    oauth_client_secret: str = ""
    oauth_client_secret_env: str = ""
    #: Authorize-URL parameters a vendor requires. Google needs `access_type=offline` and
    #: `prompt=consent`, or it returns no refresh token, and the connection then dies an hour
    #: after sign-in with nothing in the logs to say why.
    pkce_auth_extra: tuple = ()
    #: Whose sign-in a PKCE connector stores, which decides where the tokens land and how they
    #: are renewed: "google" (`google_auth`) or "microsoft" (`microsoft_auth`).
    pkce_provider: str = "google"
    #: The vendor's own authorize page hands a long-lived user token to the app's public key,
    #: with no OAuth exchange (Trello's `/1/authorize`). One click opens it and the
    #: token lands in the connector's own fields; `oauth_client_id` carries the key.
    browser_token_auth: bool = False
    #: The MCP server takes an API key the person pastes into the card, sent as `Authorization:
    #: Bearer`. There is no browser and no registration, so the card offers the manual field only
    #: and `one_click` stays off.
    mcp_api_key_auth: bool = False
    #: A vendor server that groups its tools into toolsets the client selects (GitHub's
    #: `X-MCP-Toolsets`). It holds the connector's toolset module (`github_toolsets.py`): the
    #: catalogue, the profile's choice, the header, and the scopes each set needs. The choice lives
    #: in the profile and is applied again at every session build, like the URL.
    mcp_toolsets: object = None
    #: Gate this vendor's tools by the server's own `readOnlyHint`: a read-only tool runs and
    #: anything else asks. Off by default, because the hint is only the server's claim and a claim
    #: never clears a gate on its own. It is set for a first-party server the descriptor names,
    #: where the alternative is a `requires_approval: false` entry that lets every write through.
    mcp_approval_by_hints: bool = False
    #: A first-party MCP server that runs on this machine, one per session in the session's
    #: folder: a callable returning its launch (`{"command", "args"}`), or None where it cannot run.
    #: The session plan fills the entry in from it on every turn, as it does a vendor's URL (the
    #: personal WhatsApp connector).
    mcp_local: object = None

    # -- risk and accounts ------------------------------------------------------------------

    #: Experimental connectors are hidden until the person enables them, need an explicit
    #: acknowledgement of the risk to connect, and ship in a separate package that release builds
    #: leave out entirely.
    experimental: bool = False
    risk_notice: str = ""
    #: The credentials field that names an account (`project_id`, say), or `"@identity"` for the
    #: identity string the validator returned. When set, profiles live at `<name>:account:<id>`
    #: and `:default` only points at one. Empty means a single-profile connector.
    account_field: str = ""
