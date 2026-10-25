"""Connectors that sit on a vendor's service: GitHub, Outlook, Atlassian, TCBS, Tavily, Firecrawl,
Redmine, Postman, Upwork, Canva, Figma and Notion."""

from __future__ import annotations

from .. import github_toolsets
from ..descriptor.model import ConnectorDescriptor, Field, MCPVariant
from ..descriptor.validators import (
    validate_canva,
    validate_figma,
    validate_firecrawl,
    validate_notion,
    validate_redmine,
    validate_trello,
)


def _github() -> ConnectorDescriptor:
    # GitHub signs in with the DEVICE grant and not dynamic registration. Its authorization server
    # publishes no `registration_endpoint`, so registration is out, and its token endpoint wants a
    # client secret even with PKCE, so the authorization-code grant is out for a desktop app. The
    # device grant is the one that needs no secret (see mcp/device_auth.py).
    #
    # It also lost its inbound half. Mentions used to arrive as relay frames, and a laptop has no
    # public URL for GitHub to post webhooks to, so the connector is no longer two-way: the agent
    # calls out to GitHub and nothing calls in.
    return ConnectorDescriptor(
        name="github",
        title="GitHub",
        icon="⌘",
        blurb="Work with issues, pull requests, repository files and CI status.",
        auth="token",
        two_way=False,
        brand_color="#1f2328",
        logo="github",
        fields=[Field("token", "Personal access token", secret=True, help="A fine-grained or classic GitHub token.")],
        instructions=[
            "Create a GitHub personal access token that can reach the repositories you want.",
            "For write actions add Issues or Pull Requests write permission as needed.",
            "The token is used against GitHub's own MCP server, so the agent gets the tools GitHub "
            "serves and not a hand-picked few.",
        ],
        available=True,
        mcp_url="https://api.githubcopilot.com/mcp/",
        mcp_toolsets=github_toolsets,
        mcp_approval_by_hints=True,
        mcp_device_auth=True,
        mcp_device_endpoint="https://github.com/login/device/code",
        oauth_token_endpoint="https://github.com/login/oauth/access_token",
        # The default leans read-heavy. Widening the grant is for whoever registers the OAuth App
        # to decide, and this file should not escalate it quietly.
        oauth_scope="repo read:org",
        # One click needs an OAuth App with device flow enabled, its client id set as
        # `GITHUB_OAUTH_CLIENT_ID` in the environment or the state folder's `.env`; none ships in
        # the source. Each person still signs in to their OWN GitHub account and gets their own
        # token. Device flow and not a browser redirect, because exchanging an authorization code
        # with a client id and PKCE but no secret is refused with `incorrect_client_credentials`:
        # GitHub does not tell public clients from confidential ones, so a desktop app that cannot
        # keep a secret has exactly one secret-free grant.
        oauth_client_id_env="GITHUB_OAUTH_CLIENT_ID",
    )


def _atlassian() -> ConnectorDescriptor:
    # The internal name stays "jira" on purpose. It is the connector id, the mcp.json server key,
    # the `mcp__jira__*` tool prefix and the `mcp-oauth:jira` token profile all at once, so
    # renaming it would orphan every one of them on an existing install for a cosmetic gain. A
    # person only ever reads `title`.
    return ConnectorDescriptor(
        name="jira",
        title="Atlassian",
        icon="◆",
        blurb="Jira, Confluence and Bitbucket, covered by one sign-in.",
        auth="api_token",
        two_way=False,
        brand_color="#0052cc",
        logo="jira",
        aliases=(
            "jira",
            "confluence",
            "bitbucket",
            "rovo",
            "jsm",
            "issues",
            "tickets",
            "wiki",
            "pages",
            "pull requests",
            "repos",
            "project management",
        ),
        # Version 2 with `tools=all` on purpose: v2 defaults to a discover-and-execute layer that
        # lists only its "Primary" tools up front. `tools=all` returns the flat, paginated list
        # (see mcp/client.py, whose pagination is why the list arrives whole).
        mcp_url="https://mcp.atlassian.com/v2/mcp?tools=all",
        fields=[
            Field("base_url", "Atlassian site URL", secret=False, help="For example https://example.atlassian.net"),
            Field("email", "Account email", secret=False),
            Field("api_token", "API token", secret=True, help="An Atlassian API token."),
        ],
        instructions=[
            "One click signs in with Atlassian in your browser and covers Jira, Confluence and Bitbucket together (recommended).",
            "Manual: an Atlassian API token reaches JIRA ONLY. It is the way in for Server and Data "
            "Center sites, which the hosted MCP server does not serve.",
        ],
        available=True,
    )


def _tcbs() -> ConnectorDescriptor:
    # Vietnamese securities data from TCInvest. Its dynamic registration is a STUB: every caller
    # gets the same `client_id: "tcinvest"`, a `client_secret: "dummy"` and a `redirect_uris`
    # that points at TCBS's own callback, not the one sent. `/authorize` ignores both `client_id`
    # and `redirect_uri` too, and forwards to TCBS's real upstream under a fixed client with an
    # opaque `state` of its own.
    #
    # It still works for us, because the MCP SDK builds the authorize URL and the token exchange
    # from `client_metadata.redirect_uris[0]`, which is OUR loopback, and not from whatever
    # `register` echoed back. TCBS keeps our redirect and state against its opaque state on the
    # server side and bounces the browser home afterwards. That last hop is the one thing not
    # verified by probing, since completing it needs a real TCBS login and an iOTP.
    return ConnectorDescriptor(
        name="tcbs",
        title="Techcom Securities (TCBS)",
        icon="₫",
        blurb="Công ty Cổ phần Chứng khoán Kỹ Thương. Vietnamese stock market data from TCInvest: "
        "company profiles, financial statements, valuation, technical ratings and market news.",
        auth="oauth",
        two_way=False,
        brand_color="#ed1d24",  # the red of their own logo file and not of the site chrome
        logo="tcbs",
        aliases=(
            "chứng khoán",
            "chung khoan",
            "cổ phiếu",
            "co phieu",
            "stock",
            "stocks",
            "tcinvest",
            "techcombank",
            "vietnam",
            "finance",
            "market",
            "valuation",
        ),
        mcp_url="https://mcp.tcbs.com.vn/mcp/tcinvest/",
        fields=[],
        instructions=[
            "One click opens TCBS sign-in in your browser; approve it with your iOTP.",
            "The sign-in happens entirely on this computer, and the tokens stay here.",
            "You need a TCInvest account.",
        ],
        available=True,
    )


def _firecrawl() -> ConnectorDescriptor:
    # Firecrawl's own remote MCP server answers `initialize` with 200 and no `www-authenticate`:
    # anonymous is a real tier there. An unauthenticated `tools/list` returns 3 read-only tools
    # (scrape, search, parse) that do work, and any Authorization header at all returns 25.
    #
    # That is why this is NOT the ordinary `auth="oauth"` MCP path. The SDK starts OAuth only
    # after a 401, which this server never sends, so it would connect anonymously, report success
    # and hand over 3 tools of 25 with nothing on screen to say so. The pasted key makes the
    # difference, and Firecrawl's own documentation calls this API Key Mode.
    return ConnectorDescriptor(
        name="firecrawl",
        title="Firecrawl",
        icon="◎",
        blurb="Turn a page into markdown, crawl a whole site, map its URLs and watch pages for changes.",
        auth="oauth",
        two_way=False,
        brand_color="#fa5d19",
        logo="firecrawl",
        aliases=("scrape", "scraping", "crawl", "crawler", "web", "website", "extract", "markdown", "monitor"),
        mcp_url="https://mcp.firecrawl.dev/v2/mcp",
        mcp_api_key_auth=True,
        fields=[
            Field("api_key", "API key", secret=True, help="From the Firecrawl dashboard, under API Keys.", placeholder="fc-…")
        ],
        validate=validate_firecrawl,
        instructions=[
            "Get an API key at firecrawl.dev, under API Keys.",
            "Paste it below. The key stays on this computer.",
            "The key unlocks crawl, map, monitors and the research tools; without it the server offers only scrape, search and parse.",
        ],
        available=True,
    )


def _redmine() -> ConnectorDescriptor:
    # Self-hosted, so the site URL is a field and not a constant. Authentication is a per-user API
    # key in a header: no OAuth, no app registration, and nothing to approve on a vendor console.
    return ConnectorDescriptor(
        name="redmine",
        title="Redmine",
        icon="◈",
        blurb="Work with issues, projects and time logs on your Redmine site.",
        auth="api_token",
        two_way=False,
        brand_color="#b32024",
        logo="redmine",
        aliases=(
            "issue tracker",
            "issues",
            "tickets",
            "bug tracker",
            "project management",
            "time tracking",
            "timesheet",
        ),
        fields=[
            Field(
                "base_url",
                "Redmine URL",
                secret=False,
                help="The root of the site, with no path after it.",
                placeholder="https://redmine.example.com",
            ),
            Field("api_key", "API key", secret=True, help="My account -> API access key, in the panel on the right."),
        ],
        instructions=[
            "An administrator has to enable the REST API first: Administration -> Settings -> API.",
            "Open My account in Redmine and copy the API access key from the panel on the right.",
            "Paste your Redmine URL and the API key below.",
        ],
        validate=validate_redmine,
        available=True,
    )


def _trello() -> ConnectorDescriptor:
    # Tools of our own on Trello's REST API, not Trello's MCP server: that server
    # groups actions a per-tool approval cannot tell apart and lacks comments, attachments,
    # custom fields, labels and members. Two ways in, one store: one click with the build's own
    # API key (Trello's authorize page hands back a token), or the person's own key and token.
    return ConnectorDescriptor(
        name="trello",
        title="Trello",
        icon="▤",
        blurb="Work with your boards, lists and cards: plan, assign, comment, attach and check off.",
        auth="api_token",
        two_way=False,
        brand_color="#0079bf",
        logo="trello",
        aliases=("kanban", "board", "boards", "cards", "tasks", "project management", "todo"),
        fields=[
            Field(
                "api_key",
                "API key",
                secret=False,
                help="From trello.com/power-ups/admin: open your Power-Up, then API key.",
                placeholder="32 letters and digits",
            ),
            Field("token", "Token", secret=True, help="The Token link beside the API key makes one for your account."),
        ],
        # The manual steps only: the one-click pane says what its own button does.
        instructions=[
            "Open trello.com/power-ups/admin and create a Power-Up (any name) in one of your workspaces.",
            "Open its API key tab, generate a key, and copy it.",
            "Follow the Token link beside the key, press Allow, and copy the token. Paste both below.",
        ],
        validate=validate_trello,
        browser_token_auth=True,
        # One click needs a Power-Up's API key as `REEVE_TRELLO_API_KEY` in the environment or the
        # state folder's `.env`; none ships in the source. A token is still needed for anything,
        # and that stays local.
        oauth_client_id_env="REEVE_TRELLO_API_KEY",
        # One account per Trello member, named by the username the validator returns.
        account_field="@identity",
    )


def _postman() -> ConnectorDescriptor:
    # Postman's own remote MCP server, checked against the live endpoints. Each answers 401 with
    # RFC 9728 `resource_metadata`; the authorization server advertises `registration_endpoint`,
    # PKCE S256 and `token_endpoint_auth_methods: none`; and an anonymous registration returned 201
    # with a client id and no secret. So it has the one-click shape of Atlassian and Notion.
    #
    # There are THREE endpoints and the person chooses. The counts come from the vendor's source
    # (`src/enabledResources.ts`), since their documentation says only "100+":
    #
    #     /minimal   42 listed / 41 served   19 write   0 delete
    #     /mcp      140 listed               76 write  17 delete  (deleteWorkspace...)
    #     /code      23 listed / 23 served    0 write   0 delete
    #
    # "Served" is what `tools/list` returned on a live connection. Essentials lists 42 and serves
    # 41, because one entry in their source is an orchestrator that folds subtools, so the card
    # quotes 41, the number the person will see. Full is not measured on the wire: connecting to
    # it just to count would grant 17 delete tools over a real account.
    #
    # Essentials is the default because of the last column and not the first. It is no cut-down
    # Full, since it still creates, updates, syncs and runs; it merely has no tool that destroys
    # anything, the right floor for an agent taking multi-step turns in someone's real API
    # workspace.
    #
    # `/learn` (one tool, documentation search) is not offered: it is a separate OAuth resource,
    # so bundling it with Essentials would cost a second sign-in for one tool, and it is already
    # inside Full. The EU host (mcp.eu.postman.com) is not offered either, since it supports API
    # keys only and no OAuth; that is the api-key path, which excludes one click by design.
    return ConnectorDescriptor(
        name="postman",
        title="Postman",
        icon="◈",
        blurb="Work with your API collections, environments, mocks and specs, and run them.",
        auth="oauth",
        two_way=False,
        brand_color="#ff6c37",
        logo="postman",
        aliases=("api", "collections", "rest", "testing", "mocks", "openapi", "specs"),
        mcp_url="https://mcp.postman.com/minimal",
        mcp_variants=(
            MCPVariant("minimal", "Essentials", "https://mcp.postman.com/minimal",
                       "41 tools to read, create, update and run. Nothing that deletes."),
            MCPVariant("full", "Full", "https://mcp.postman.com/mcp",
                       "140 tools, everything, including deleting collections and workspaces."),
            MCPVariant("code", "Code generation", "https://mcp.postman.com/code",
                       "23 tools, all read-only, for generating client code from an API definition."),
        ),
        mcp_variant_default="minimal",
        fields=[],
        instructions=[
            "Choose how much access the agent gets, then connect. The sign-in opens in your "
            "browser and happens entirely on this computer.",
            "Changing the choice later means connecting again, because each option is its own "
            "endpoint with its own sign-in.",
        ],
        available=True,
    )


def _upwork() -> ConnectorDescriptor:
    # Upwork's own remote MCP server, probed live (their page answers 403 to anything but a
    # browser): a POST to /mcp gives 401 with RFC 9728 `resource_metadata`; the protected-resource
    # document names the authorization server; that one offers PKCE S256, `token_endpoint_auth
    # "none"` and a `registration_endpoint`; and a POST to the registration endpoint returned 201
    # with a client id.
    #
    # The last step was worth doing. The advertised registration endpoint is
    # `https://www.upwork.com/register`, which reads like their human signup page, and Asana had
    # already shown that a vendor can advertise registration metadata its server does not honour.
    # This one does: an anonymous public client registered and came back with an id and no secret.
    # The authorize and token endpoints live on www.upwork.com and not on the MCP host, which is
    # legal, and is why discovery has to be followed and not guessed from the server URL.
    return ConnectorDescriptor(
        name="upwork",
        title="Upwork",
        icon="⬘",
        blurb="Find and save jobs, manage proposals, contracts and milestones, and message clients.",
        auth="oauth",
        two_way=False,
        brand_color="#14a800",
        logo="upwork",
        aliases=("freelance", "contracts", "proposals", "jobs", "gigs", "invoices"),
        mcp_url="https://mcp.upwork.com/mcp",
        fields=[],
        instructions=[
            "One click signs you in at Upwork in your browser, and the tokens stay on this computer.",
            "The agent acts as you: it can see and change what your own Upwork account can.",
        ],
        available=True,
    )


def _canva() -> ConnectorDescriptor:
    return ConnectorDescriptor(
        name="canva",
        title="Canva",
        icon="◠",
        blurb="Browse, create and export designs.",
        auth="oauth",
        two_way=False,
        brand_color="#00c4cc",
        logo="canva",
        fields=[Field("access_token", "OAuth access token", secret=True, help="The access token of a Canva Connect integration.")],
        instructions=[
            "Create a Connect integration at canva.com/developers and complete an OAuth grant.",
            "Paste the access token below.",
        ],
        validate=validate_canva,
        available=True,
    )


def _figma() -> ConnectorDescriptor:
    return ConnectorDescriptor(
        name="figma",
        title="Figma",
        icon="◐",
        blurb="Read design files and comments, and export assets.",
        auth="api_token",
        two_way=False,
        brand_color="#f24e1e",
        logo="figma",
        fields=[
            Field(
                "access_token",
                "Personal access token",
                secret=True,
                help="Figma → Settings → Security → Personal access tokens.",
                placeholder="figd_…",
            )
        ],
        instructions=[
            "In Figma open Settings → Security and generate a personal access token.",
            "Paste it below.",
        ],
        validate=validate_figma,
        available=True,
    )


def _notion() -> ConnectorDescriptor:
    # Notion runs an official remote MCP server that advertises a dynamic-registration endpoint
    # (checked in its /.well-known/oauth-authorization-server), which is what a fully local
    # one-click needs, with no broker and no Reeve account. One MCP server entry is one
    # connection, so this path serves a single workspace.
    return ConnectorDescriptor(
        name="notion",
        title="Notion",
        icon="◰",
        blurb="Search pages, read their content, query databases and create pages.",
        auth="oauth",
        two_way=False,
        mcp_url="https://mcp.notion.com/mcp",
        fields=[
            Field(
                "access_token",
                "Integration secret",
                secret=True,
                help="From an internal integration at notion.so/my-integrations; share the pages it "
                "should see with that integration.",
                placeholder="ntn_…",
            )
        ],
        instructions=[
            "One click signs in with Notion in your browser. No token is typed and no Reeve account "
            "is needed; the sign-in happens entirely on this computer.",
            "Manual: create an internal integration at notion.so/my-integrations,",
            "copy its secret, and share the pages it should see with the integration.",
        ],
        validate=validate_notion,
        brand_color="#1f2328",
        logo="notion",
        # A connection is keyed by the workspace id; a manual integration token falls back to the
        # workspace name the validator returns.
        account_field="account_id",
    )


def build() -> list[ConnectorDescriptor]:
    return [
        _github(),
        _atlassian(),
        _tcbs(),
        _firecrawl(),
        _redmine(),
        _trello(),
        _postman(),
        _upwork(),
        _canva(),
        _figma(),
        _notion(),
    ]
