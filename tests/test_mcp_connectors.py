"""Connectors that are a vendor's MCP server: the descriptor names the vendor's URL,
connecting writes the server's config and runs the local sign-in, and the Connectors page owns it from
then on (the MCP tab in Settings does not list it). A conversation is given the vendor's whole catalog,
held back by which connectors it has and by the approval each tool's class asks for.

The vendor's catalog can change under us, so every gate here fails closed, and nothing at the start
of a turn may open a browser.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from harness.extensions.mcp import oauth as mcp_oauth
from harness.extensions.mcp.config import put_global_server, read_global
from harness.integrations.connectors.catalog import tool_dicts
from harness.integrations.connectors.descriptors import get_descriptor, list_descriptors
from harness.integrations.connectors.lifecycle import connector_list, disconnect_connector
from harness.interface.server.manager import SessionManager
from harness.safety.secrets import SecretStore

SIGNS_IN = {"url": "https://mcp.example.com/mcp", "auth": "oauth", "enabled": True}
UPWORK = {"url": "https://mcp.upwork.com/mcp", "auth": "oauth"}


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)


def _manager(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    return SessionManager(data_dir=tmp_path / "data")


def _connect_answers(manager, monkeypatch, answer):
    async def connect(name):
        return answer

    monkeypatch.setattr(manager, "connect_mcp", connect)


def _row(secrets, name):
    return {c["name"]: c for c in connector_list(secrets)}[name]


def test_atlassian_access_copy_names_every_product_the_pin_reaches():
    """The Access bullets are the LAST thing a user reads before approving an OAuth that
    grants all three products. Naming only Jira understates the grant — and now that the
    session takes the vendor's whole catalog, the copy is the only place the breadth is
    stated at all."""
    from harness.integrations.connectors.catalog_copy import ABOUT, ACCESS

    bullets = " ".join(ACCESS["jira"]).lower()
    for product in ("jira", "confluence", "bitbucket"):
        assert product in bullets, f"Access copy never mentions {product}"
    assert "confluence" in ABOUT["jira"].lower()
    assert "bitbucket" in ABOUT["jira"].lower()


def test_atlassian_card_names_the_products_its_one_signin_covers():
    """One Atlassian OAuth grants Jira + Confluence + Bitbucket at once. A card labelled
    only "Jira" understates what the consent screen is about to ask for, and a user who
    wants Confluence would never find it."""
    from harness.integrations.connectors.descriptors import get_descriptor

    d = get_descriptor("jira")
    assert d.title == "Atlassian"
    for term in ("confluence", "bitbucket", "jira"):
        assert term in d.aliases, f"catalog search for {term!r} must surface this card"
        assert term in (d.blurb + " " + " ".join(d.aliases)).lower()


def test_a_stale_seeded_url_is_overridden_by_the_descriptor(tmp_path, monkeypatch):
    """An install that connected before an endpoint move has the OLD url cached in
    mcp.json. The pin self-heals from the descriptor every session; if the url did not,
    that install would aim a v2 pin at a v1 server and filter every tool away while
    still reading "connected"."""
    _state(tmp_path, monkeypatch)
    monkeypatch.setenv("REEVE_HOME", str(tmp_path))
    put_global_server(
        "jira",
        {
            "url": "https://mcp.atlassian.com/v1/mcp",  # what an older connect seeded
            "auth": "oauth",
            "include_tools": ["getVisibleJiraProjects"],
            "enabled": True,
        },
    )
    mgr = SessionManager(workspace=str(tmp_path))
    mgr.effective_connectors = lambda *a, **k: {"jira"}  # type: ignore[method-assign]
    monkeypatch.setattr(
        "harness.extensions.mcp.oauth.has_tokens", lambda name, secrets: True
    )
    plan = {s.name: s for s in mgr._mcp_plan("sess", None, "code")}
    assert "jira" in plan, "the backed server should be in the plan"
    assert plan["jira"].url == "https://mcp.atlassian.com/v2/mcp?tools=all"
    # and the allowlist that install cached is dropped, not carried forward
    assert plan["jira"].include_tools is None


def test_every_mcp_backed_descriptor_has_a_sign_in_that_can_complete():
    """A `mcp_url` with no workable grant is a card that opens a browser and dead-ends.

    Two grants are in use, and each vendor gets the one its authorization server permits
    (checked against published metadata, 2026-09-05):

      DCR      atlassian / notion / tcbs / postman / upwork — `registration_endpoint`
                                              so no client is registered up front at all
                                              (TCBS's is a stub that echoes a fixed client
                                              and its own redirect; the SDK still signs in
                                              with OUR loopback, which is what matters.
                                              Postman was checked by REGISTERING, not by
                                              reading metadata: an anonymous client posted
                                              to /register returned 201 with a client id
                                              and no secret, 2026-09-08. Upwork the same
                                              way, 2026-09-10 — worth doing there because
                                              its advertised endpoint is
                                              www.upwork.com/register, which reads like a
                                              human signup page and is not)
      device   github                       — no DCR, AND a token endpoint that demands
                                              a secret even with PKCE
      api key  firecrawl                    — firecrawl answers 200 unauthenticated,
                                              so the SDK's 401-triggered flow never
                                              fires and it would connect anonymously
                                              with 3 tools of 25. The pasted key rides
                                              as a bearer instead (mcp/api_key_auth.py)

    Adding a connector here without first confirming which bucket it falls in is how you
    ship a Connect button that ends on "we couldn't identify the app requesting access".
    """
    backed = {d.name: d for d in list_descriptors() if d.mcp_url}
    assert set(backed) == {
        "jira",
        "upwork",
        "notion",
        "github",
        "tcbs",
        "postman",
        "upwork",
        "firecrawl",
    }, "a new MCP-backed connector must be verified DCR- or device-capable first"
    for name in ("jira", "upwork", "notion", "tcbs"):
        d = backed[name]
        assert not d.mcp_device_auth and not d.pkce_auth, f"{name} uses DCR"

    for name in ("firecrawl",):
        k = backed[name]
        assert k.mcp_api_key_auth is True and not k.mcp_device_auth
        # A key, not a grant: the card must offer the field and no browser button.
        assert [x.key for x in k.fields] == ["api_key"] and k.validate is not None

    g = backed["github"]
    assert g.mcp_device_auth is True and g.pkce_auth is False
    # useless without its endpoints and a place to read its id
    assert g.mcp_device_endpoint and g.oauth_token_endpoint and g.oauth_client_id_env

    assert "asana" not in backed


def test_the_google_connectors_sign_in_by_pkce_and_serve_their_own_tools():
    """Google runs remote MCP servers for these, and we deliberately do not use them.

    Every `tools/call` against them requires the Cloud project to be enrolled in the
    Workspace Developer Preview Program (2026-09-06). The product APIs behind them carry
    no such gate, so the tools are hand-written — but the browser SIGN-IN is kept, because
    the alternative is a pasted access token that dies after one hour.

    So: PKCE fields present, `mcp_url` absent, and real tools in the catalog. A future
    edit that restores `mcp_url` here would silently blank the tool list, since
    `active_tool_defs` returns nothing for a profile in "mcp" mode.
    """
    from harness.integrations.connectors.catalog import TOOLS_BY_CONNECTOR

    google = [d for d in list_descriptors() if d.name.startswith("google_")]
    assert {d.name for d in google} == {
        "google_calendar",
        "google_drive",
        "google_docs",
        "google_sheets",
        "google_slides",
    }
    for d in google:
        assert d.pkce_auth is True, d.name
        assert not d.mcp_url, f"{d.name} would lose its hand-written tools"
        assert d.pkce_auth_endpoint and d.oauth_token_endpoint, d.name
        assert d.oauth_scope and d.oauth_client_id_env, d.name
        assert TOOLS_BY_CONNECTOR.get(d.name), f"{d.name} has no tools"

    # The surface mirrors what Google's own MCP servers offer, tool for tool.
    assert {d.name: len(TOOLS_BY_CONNECTOR[d.name]) for d in google} == {
        "google_calendar": 9,
        "google_drive": 8,
        "google_docs": 2,
        "google_sheets": 6,
        "google_slides": 2,
    }


def test_device_connect_refuses_without_a_client_id(tmp_path, monkeypatch):
    """The device grant needs a PUBLIC client id, and the OAuth App belongs to whoever
    ships the build. Unset must fail with an instruction, not a half-started flow that
    dead-ends on the vendor's page."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    # A fork, or a build with the id pulled. Patched rather than unset so this never
    # reaches github.com — a test that opens a real device flow is a test that flakes.
    monkeypatch.setattr(manager, "device_client_id", lambda d: "")
    out = asyncio.run(manager.mcp_connect_connector("github"))
    assert not out["ok"]
    assert "GITHUB_OAUTH_CLIENT_ID" in out["error"]
    assert "Manual" in out["error"], "a user with no client id needs the way out named"
    # and nothing was seeded — a failed start must leave no enabled server behind
    assert "github" not in read_global()


def test_a_pasted_token_reaches_the_vendors_mcp_catalog(tmp_path, monkeypatch):
    """A PAT is a bearer, and these servers accept one. So the MANUAL path reaches the
    same catalog one-click does — otherwise GitHub's MCP would be hostage to registering
    an OAuth App, while the pasted token drove eight hand-written REST tools instead."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"type": "token", "token": "ghp_x"})

    async def fake_connect(name):
        return {"ok": True, "tools": 42}

    monkeypatch.setattr(manager, "connect_mcp", fake_connect)
    out = asyncio.run(manager.arm_bearer_mcp("github"))
    assert out["ok"] and out["tools"] == 42

    raw = read_global()["github"]
    assert raw["url"] == "https://api.githubcopilot.com/mcp/"
    assert raw["auth"] == "device"
    assert "include_tools" not in raw
    # the token became the bearer the transport reads, and never entered mcp.json
    from harness.extensions.mcp.device_auth import bearer_header

    assert bearer_header("github", manager.secrets) == {"Authorization": "Bearer ghp_x"}
    assert "ghp_x" not in json.dumps(raw)
    assert manager.secrets.get("github:default")["mode"] == "mcp"


def test_a_failed_arm_leaves_the_rest_tools_working(tmp_path, monkeypatch):
    """If the server rejects the token, the connector must fall back to what it had —
    NOT end up in mcp mode with no reachable tools, which is what setting `mode` before
    a successful connect would produce."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"type": "token", "token": "ghp_bad"})

    async def fail_connect(name):
        return {"ok": False, "error": "401 bad credentials"}

    monkeypatch.setattr(manager, "connect_mcp", fail_connect)
    out = asyncio.run(manager.arm_bearer_mcp("github"))
    assert not out["ok"] and "401" in out["error"]
    assert "github" not in read_global()
    profile = manager.secrets.get("github:default")
    assert profile.get("mode") != "mcp"
    # The hand-written issue/PR tools are gone for a token connection (the vendor server
    # is the tool set, 2026-09-30); only what runs on this machine is still ours.
    names = [t["name"] for t in tool_dicts(manager.secrets, "github")]
    assert names == ["github_clone", "github_pull"]


def test_an_mcp_connected_account_connector_reads_as_connected(tmp_path, monkeypatch):
    """Notion is BOTH multi-account and MCP-backed, and the account block overwrites
    `connected` from account rows an MCP connect never writes. That left the card
    reporting disconnected — so the modal polled forever — and `enabled` false, which
    cut its tools from sessions entirely, all while a live connection served 42 tools
    (owner-hit 2026-09-06)."""
    from harness.integrations.connectors import connector_list

    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    secrets.put("notion:default", {"mode": "mcp", "enabled": True})
    secrets.put("mcp-oauth:notion", {"tokens": {"access_token": "nt_x"}})

    row = {c["name"]: c for c in connector_list(secrets)}["notion"]
    assert row["connected"] is True, "MCP tokens ARE the connection for an mcp profile"
    assert row["enabled"] is True, "a disabled connector is dropped from sessions"

    # signing out takes it back down — tokens are the source of truth, not account rows
    secrets.delete("mcp-oauth:notion")
    row = {c["name"]: c for c in connector_list(secrets)}["notion"]
    assert row["connected"] is False


def test_the_account_path_still_works_for_a_non_mcp_profile(tmp_path, monkeypatch):
    """The guard must not disable multi-account connectors that are NOT in mcp mode —
    a manual Notion integration token still connects the ordinary way."""
    from harness.integrations.connectors import connect_connector, connector_list

    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    connect_connector(
        secrets, "notion", {"access_token": "ntn_manual"}, validate=False
    )
    row = {c["name"]: c for c in connector_list(secrets)}["notion"]
    assert row["connected"] is True
    assert row["accounts"], "account rows are still how a manual connect registers"


def test_github_one_click_comes_from_the_operators_env_file(tmp_path, monkeypatch):
    """No client id ships in the source tree: one committed there is in every clone and every
    secret scan of it. One click is offered once the operator puts an id in the state folder's
    `.env`; before that the manual way is all the card offers."""
    from harness.integrations.connectors import connector_list
    from harness.integrations.connectors.descriptors import get_descriptor

    _state(tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    assert not get_descriptor("github").oauth_client_id
    rows = lambda: {c["name"]: c for c in connector_list(SecretStore())}  # noqa: E731
    assert rows()["github"]["one_click"] is False
    (tmp_path / "state" / ".env").write_text("GITHUB_OAUTH_CLIENT_ID=Iv1.mine\n", encoding="utf-8")
    assert rows()["github"]["one_click"] is True


def test_one_click_is_hidden_when_it_cannot_run(tmp_path, monkeypatch):
    """A device-grant connector with no client id anywhere cannot start a flow. Showing
    a One-click tab whose only outcome is an error message is a UI that lies; the manual
    path is what the user should be offered (owner-hit 2026-09-05).

    DCR connectors are unaffected — needing nothing up front is the point of DCR.
    """
    from harness.integrations.connectors import connector_list

    _state(tmp_path, monkeypatch)
    secrets = SecretStore()

    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    # simulate a build that shipped no id (a fork, or the id pulled)
    monkeypatch.setattr(
        "harness.integrations.connectors.lifecycle.listing._mcp_one_click_ready",
        lambda d: not d.mcp_device_auth,
    )
    rows = {c["name"]: c for c in connector_list(secrets)}
    assert rows["github"]["one_click"] is False
    # DCR connectors need nothing up front, so they are unaffected
    assert rows["notion"]["one_click"] is True and rows["jira"]["one_click"] is True


def test_the_device_client_id_is_the_env_then_the_env_file_then_the_descriptors(tmp_path, monkeypatch):
    """The process environment wins, the state folder's `.env` is next, and an id a descriptor
    carries itself is the last resort (the source tree's descriptors carry none)."""
    _state(tmp_path, monkeypatch)
    from harness.integrations.connectors.descriptors import get_descriptor

    manager = SessionManager(data_dir=tmp_path / "data")
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    built = SimpleNamespace(oauth_client_id="Iv1.built", oauth_client_id_env="GITHUB_OAUTH_CLIENT_ID")
    assert manager.device_client_id(built) == "Iv1.built"

    (tmp_path / "state" / ".env").write_text("GITHUB_OAUTH_CLIENT_ID=Iv1.file\n", encoding="utf-8")
    assert manager.device_client_id(built) == "Iv1.file"

    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.mine")
    assert manager.device_client_id(built) == "Iv1.mine"
    assert get_descriptor("github").oauth_client_id_env == "GITHUB_OAUTH_CLIENT_ID"


def test_device_connect_returns_the_code_before_polling(tmp_path, monkeypatch):
    """The user code has to reach the screen IMMEDIATELY — they are about to paste it —
    so the request half is awaited and only the polling detaches."""
    _state(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.public")
    manager = SessionManager(data_dir=tmp_path / "data")

    from harness.extensions.mcp import device_auth as da

    async def fake_request(**kw):
        assert "secret" not in str(kw).lower()
        return da.DeviceCode(
            {
                "device_code": "dc",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "interval": 999,  # the background poll must not actually run here
            }
        )

    monkeypatch.setattr("harness.extensions.mcp.device_auth.request_code", fake_request)

    async def run():
        out = await manager.mcp_connect_connector("github")
        # let the detached task start and park on its first sleep
        await asyncio.sleep(0)
        return out

    out = asyncio.run(run())
    assert out["ok"] and out["device"]["user_code"] == "WDJB-MJHT"
    assert out["device"]["needs_code_entry"] is True
    # not connected yet: the server is armed only after the user approves
    assert "github" not in read_global()


def test_device_finish_arms_the_server_once_the_user_approves(tmp_path, monkeypatch):
    """The half after approval used to raise NameError (`mcp_url_for` never imported), so a
    signed-in user saw GitHub stay disconnected. Proven by running it to the end: the token
    is stored, the server entry is written, and connect_mcp decides the mode."""
    _state(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.public")
    manager = SessionManager(data_dir=tmp_path / "data")
    d = get_descriptor("github")

    async def fake_poll(**kw):
        return {"access_token": "gho_invented", "token_type": "bearer", "scope": "repo"}

    async def fake_connect(name):
        assert "github" in read_global()  # armed before the connection is tried
        return {"ok": True}

    monkeypatch.setattr("harness.extensions.mcp.device_auth.poll_for_token", fake_poll)
    monkeypatch.setattr(manager, "connect_mcp", fake_connect)
    code = SimpleNamespace(device_code="dc")

    asyncio.run(manager._device_finish(d, "Iv1.public", code))

    assert read_global()["github"]["url"] == d.mcp_url
    assert (manager.secrets.get("github:default") or {}).get("mode") == "mcp"
    assert "github" not in manager._mcp_errors


def test_mcp_backed_connectors_declare_no_tools_of_their_own():
    """Backed by MCP means the vendor's catalog, whole. A tool definition here would be an allowlist
    by another name: what dropped three Atlassian tools without a word when v2 renamed them, and
    what the server's own annotations made unneeded."""
    from harness.integrations.connectors.catalog import TOOL_DEFS

    assert {d.name for d in list_descriptors() if d.mcp_url} >= {"upwork", "jira"}
    assert not [t for t in TOOL_DEFS if t.name.startswith("mcp__")], "the server is the catalog"


def test_a_connector_in_mcp_mode_declares_no_static_tools(tmp_path, monkeypatch):
    """Set up by hand a connector shows its own REST tools; in MCP mode it shows none from here, and
    the page lists what the live connection serves, so it can never claim tools the server lacks."""
    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    by_hand = [t["name"] for t in tool_dicts(secrets, "jira")]
    assert "jira_search_issues" in by_hand and not any(name.startswith("mcp__") for name in by_hand)
    secrets.put("jira:default", {"mode": "mcp"})
    assert tool_dicts(secrets, "jira") == []
    assert tool_dicts(secrets, "upwork") == []  # MCP only, with no REST tools in either mode


def test_mcp_connect_seeds_the_server_without_an_allowlist(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    _connect_answers(manager, monkeypatch, {"ok": True, "tools": 9})
    assert asyncio.run(manager.mcp_connect_connector("upwork"))["ok"]
    seeded = read_global()["upwork"]
    assert (seeded["url"], seeded["auth"], seeded["requires_approval"]) == ("https://mcp.upwork.com/mcp", "oauth", False)
    assert "include_tools" not in seeded  # nothing narrows the vendor's catalog
    assert manager.secrets.get("upwork:default")["mode"] == "mcp"
    assert not asyncio.run(manager.mcp_connect_connector("gmail"))["ok"]  # no MCP path: refused before anything starts


def test_failed_mcp_connect_removes_the_seeded_config(tmp_path, monkeypatch):
    """A connect that fails (registration refused, the browser closed) leaves no enabled server
    behind; a leftover one tries again at every session start (owner-hit 2026-07-20: a pulled asana
    attempt froze every new session)."""
    manager = _manager(tmp_path, monkeypatch)
    _connect_answers(manager, monkeypatch, {"ok": False, "error": "no DCR"})
    assert not asyncio.run(manager.mcp_connect_connector("upwork"))["ok"]
    assert "upwork" not in read_global() and manager.secrets.get("upwork:default") is None


def test_prepare_mcp_tools_never_starts_an_oauth_flow(tmp_path, monkeypatch):
    """At the start of a turn a server that signs in and has no tokens is passed over: connecting
    it would open a browser and hold the session for the whole sign-in timeout. With tokens it
    connects as usual."""
    manager = _manager(tmp_path, monkeypatch)
    put_global_server("notes", SIGNS_IN)

    async def must_not_connect(server, *, scope: str = ""):
        raise AssertionError(f"ensure() reached for {server.name}, which has no tokens")

    monkeypatch.setattr(manager.mcp, "ensure", must_not_connect)
    assert asyncio.run(manager.prepare_mcp_tools("s1")) == []
    manager.secrets.put("mcp-oauth:notes", {"tokens": {"access_token": "at"}})
    reached: list[str] = []

    async def connects(server, *, scope: str = ""):
        reached.append(server.name)
        return SimpleNamespace(tools=[])

    monkeypatch.setattr(manager.mcp, "ensure", connects)
    asyncio.run(manager.prepare_mcp_tools("s2"))
    assert reached == ["notes"]


def test_connected_follows_tokens_and_disconnect_forgets_everything(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    secrets.put("upwork:default", {"mode": "mcp", "enabled": True})
    put_global_server("upwork", UPWORK)
    marked = _row(secrets, "upwork")  # the profile alone only marks the mode
    assert marked["one_click"] is True and marked["connected"] is False
    secrets.put("mcp-oauth:upwork", {"tokens": {"access_token": "at"}})
    signed_in = _row(secrets, "upwork")
    assert signed_in["connected"] is True and signed_in["mode"] == "mcp"
    # tokens and registration, the written config and the profile all go
    assert disconnect_connector(secrets, "upwork")["ok"]
    assert secrets.get("mcp-oauth:upwork") is None and "upwork" not in read_global()
    assert _row(secrets, "upwork")["connected"] is False


def test_connector_backed_servers_hidden_from_mcp_tab(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    put_global_server("upwork", UPWORK)
    put_global_server("notes", {"url": "https://mcp.example.com/mcp", "auth": "oauth"})
    listed = [s["name"] for s in manager.list_mcp()]
    assert "notes" in listed and "upwork" not in listed


def test_a_local_connector_server_is_hidden_from_the_mcp_tab_too(tmp_path, monkeypatch):
    """WhatsApp's server is local (`mcp_local`), not a URL; it is still the connector's, and the
    Connectors page lists it once, as the connector."""
    _state(tmp_path, monkeypatch)
    assert get_descriptor("whatsapp_personal") is not None
    manager = SessionManager(data_dir=tmp_path / "data")
    put_global_server("whatsapp_personal", {"command": "python", "args": ["-m", "harness.integrations.whatsapp"]})
    put_global_server("my-tool", {"command": "python", "args": ["-m", "my_tool"]})
    names = [s["name"] for s in manager.list_mcp()]
    assert "my-tool" in names and "whatsapp_personal" not in names


def _fake_tool(name):
    return SimpleNamespace(name=name, description=f"vendor {name}", inputSchema={"type": "object"})


def test_prepare_mcp_tools_takes_the_whole_vendor_catalog(tmp_path, monkeypatch):
    """A backed server gives the conversation all the vendor returns. An `include_tools` left in
    mcp.json by the old pinned design does not filter: its names are the retired v1 ones, so it
    would leave nothing while the connector still read "connected"."""
    manager = _manager(tmp_path, monkeypatch)
    manager.secrets.put("upwork:default", {"mode": "mcp", "enabled": True})
    manager.secrets.put("mcp-oauth:upwork", {"tokens": {"access_token": "at"}})  # without tokens it is passed over
    put_global_server("upwork", {**UPWORK, "include_tools": ["manage_agent"], "enabled": True})

    async def serves(server, *, scope: str = ""):
        return SimpleNamespace(tools=[_fake_tool(n) for n in ("get_board_info", "create_item", "search")])

    monkeypatch.setattr(manager.mcp, "ensure", serves)
    monkeypatch.setattr(manager, "effective_connectors", lambda sid, agent=None: {"upwork"})
    given = {tool.__aisuite_tool_metadata__.name for tool in asyncio.run(manager.prepare_mcp_tools("s1"))}
    assert given == {"mcp__upwork__get_board_info", "mcp__upwork__create_item", "mcp__upwork__search"}
    monkeypatch.setattr(manager, "effective_connectors", lambda sid, agent=None: set())
    assert asyncio.run(manager.prepare_mcp_tools("s2")) == []  # a conversation without the connector gets none


def test_stale_token_reauth_never_opens_a_browser_mid_turn(tmp_path, monkeypatch):
    """Tokens there but the refresh refused, the SDK wants a sign-in in the browser. Outside a sign-in
    that is refused (InteractiveAuthRequired, often inside an ExceptionGroup from the anyio
    transport): the server is passed over and the failure kept (owner-hit 2026-07-20: an Atlassian
    authorize page opened at app launch from a session starting in the background)."""
    manager = _manager(tmp_path, monkeypatch)
    put_global_server("notes", SIGNS_IN)
    manager.secrets.put("mcp-oauth:notes", {"tokens": {"access_token": "stale"}})

    async def wants_a_browser(server, *, scope: str = ""):
        raise ExceptionGroup("transport", [mcp_oauth.InteractiveAuthRequired("sign-in required")])

    monkeypatch.setattr(manager.mcp, "ensure", wants_a_browser)
    assert asyncio.run(manager.prepare_mcp_tools("s1")) == []
    assert "sign-in required" in manager._mcp_errors["notes"]


def test_non_interactive_auth_wiring_refuses_the_browser():
    """Built with interactive=False, the auth never opens a browser: its redirect handler raises
    and keeps the authorize URL for the GUI to reopen, and the refusal is found bare, in a group, or
    as a cause."""
    with pytest.raises(mcp_oauth.InteractiveAuthRequired):
        asyncio.run(mcp_oauth.flow.FLOW.refuse_browser("https://vendor/authorize?x=1"))
    assert mcp_oauth.last_authorize_url() == "https://vendor/authorize?x=1"
    bare = mcp_oauth.InteractiveAuthRequired("x")
    caused = RuntimeError("wrapped")
    caused.__cause__ = bare
    for found in (bare, ExceptionGroup("g", [ValueError(), bare]), caused):
        assert mcp_oauth.is_auth_required(found)
    assert not mcp_oauth.is_auth_required(ValueError("no"))
