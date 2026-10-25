"""An MCP server authenticated with the connector's pasted API key.

Why this shape exists at all is the thing worth guarding. Firecrawl's MCP endpoint
answers `initialize` with **200 and no `www-authenticate`** — anonymous is a supported
tier there, and unauthenticated `tools/list` returns 3 working read-only tools. Send any
Authorization header and the same endpoint returns 25.

So the ordinary `auth="oauth"` path is not merely wrong here, it is invisible-wrong: the
SDK starts OAuth only after a 401, so it would connect anonymously, report success, and
serve 3 tools of 25 with nothing on screen to say so. Every assertion below exists to
keep the header on the wire.

Verified by anonymous probe, 2026-09-07.
"""

from __future__ import annotations

import httpx
import pytest

from harness.extensions.mcp.api_key_auth import api_key_header
from harness.safety.secrets import SecretStore

NAME = "firecrawl"


@pytest.fixture
def secrets(tmp_path):
    return SecretStore(tmp_path / "secrets.json")


def test_the_pasted_key_becomes_the_bearer(secrets):
    secrets.put(f"{NAME}:default", {"api_key": "fc-live-key", "enabled": True})
    assert api_key_header(NAME, secrets) == {"Authorization": "Bearer fc-live-key"}


def test_no_key_means_none_not_an_anonymous_connection(secrets):
    """None is what makes the transport refuse. Connecting without the header is not a
    smaller failure — against this server it SUCCEEDS, with a fraction of the tools."""
    assert api_key_header(NAME, secrets) is None
    secrets.put(f"{NAME}:default", {"enabled": True})
    assert api_key_header(NAME, secrets) is None


def test_a_whitespace_key_counts_as_no_key(secrets):
    """Connector profiles store "" and " " freely; either must not become `Bearer `,
    which this server would accept as a header and still answer with 25 tool names."""
    secrets.put(f"{NAME}:default", {"api_key": "   "})
    assert api_key_header(NAME, secrets) is None


def test_the_key_is_read_at_connect_time_from_the_secret_store(secrets):
    """Not copied into mcp.json, which is plain text and gets pasted between machines."""
    secrets.put(f"{NAME}:default", {"api_key": "fc-1"})
    assert api_key_header(NAME, secrets)["Authorization"].endswith("fc-1")
    secrets.put(f"{NAME}:default", {"api_key": "fc-2"})
    assert api_key_header(NAME, secrets)["Authorization"].endswith("fc-2")


# -- the transport ---------------------------------------------------------------
def test_the_transport_refuses_a_server_with_no_key():
    """Asserting on the source because reaching this branch needs a live transport.

    Scoped to the bearer branch ALONE: the device branch carries a near-identical guard
    earlier in the same function, so a whole-function search would pass with this one
    deleted — that exact mistake was made and caught by mutation on 2026-09-07.
    """
    import inspect

    from harness.extensions.mcp.client.serve import ServeMixin

    src = inspect.getsource(ServeMixin._http_credentials)
    start = src.find('if server.auth == "bearer":')
    assert start != -1, "the bearer branch is gone"
    end = src.find('if server.auth == "oauth":', start)
    assert end != -1, "the bearer branch no longer precedes the oauth one"
    branch = src[start:end]

    assert "api_key_header" in branch
    assert "has no API key" in branch, (
        "no header must mean no connection — connecting anyway is how this server "
        "silently hands back 3 tools instead of 25"
    )


def test_the_bearer_kind_is_documented_where_config_is_parsed():
    """`auth` is a free string in mcp.json; the accepted values live only in that
    comment, so a value missing from it is invisible to the next reader."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "harness/extensions/mcp/config/server.py").read_text(
        encoding="utf-8"
    )
    assert '"bearer"' in src and "api_key_auth.py" in src


# -- the card --------------------------------------------------------------------
def test_pasting_a_key_also_seeds_the_mcp_server_entry(tmp_path, monkeypatch):
    """The tools live on the vendor's server, so storing the key alone would leave the
    card green with an empty tool list. `mode: "mcp"` is what `active_tool_defs` reads."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    from harness.integrations.connectors.lifecycle import connect_connector
    from harness.extensions.mcp import config as mcp_config

    store = SecretStore(tmp_path / "secrets.json")
    assert connect_connector(store, NAME, {"api_key": "fc-x"}, validate=False)["ok"]

    entry = mcp_config.read_global().get(NAME)
    assert entry, "no MCP server entry — the connector would have no tools at all"
    assert entry["auth"] == "bearer" and entry["url"].startswith("https://mcp.firecrawl")
    assert (store.get(f"{NAME}:default") or {})["mode"] == "mcp"

    # The plumbing above is not the outcome. `mode: "mcp"` routes connected-ness to the
    # OAuth token store for every other MCP connector, and this one has no tokens — so
    # the first version of this wiring stored the key, seeded the server, and still
    # reported the card as disconnected. Assert what the user actually sees.
    from harness.integrations.connectors import connector_list

    card = {c["name"]: c for c in connector_list(store)}[NAME]
    assert card["connected"] is True, "key stored and server seeded, card still grey"
    assert card["enabled"] is True, "a disconnected card is dropped from sessions"


def test_the_card_goes_grey_again_after_disconnect(tmp_path, monkeypatch):
    """Disconnect has to take the key AND the seeded server entry, or the next session
    re-arms a server with no credential."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    from harness.integrations.connectors import connector_list
    from harness.integrations.connectors.lifecycle import connect_connector, disconnect_connector
    from harness.extensions.mcp import config as mcp_config

    store = SecretStore(tmp_path / "secrets.json")
    connect_connector(store, NAME, {"api_key": "fc-x"}, validate=False)
    disconnect_connector(store, NAME)

    card = {c["name"]: c for c in connector_list(store)}[NAME]
    assert card["connected"] is False
    assert mcp_config.read_global().get(NAME) is None
    assert api_key_header(NAME, store) is None


def test_the_card_does_not_advertise_a_browser_sign_in(tmp_path, monkeypatch):
    """There is no browser flow for this connector, so a one-click button would open
    nothing. `one_click` is keyed on `mcp_url`, which this connector has."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    from harness.integrations.connectors import connector_list

    cards = {c["name"]: c for c in connector_list(SecretStore(tmp_path / "s.json"))}
    assert cards[NAME]["one_click"] is False
    assert [f["key"] for f in cards[NAME]["fields"]] == ["api_key"]
    # the other MCP-backed connectors keep theirs
    assert cards["notion"]["one_click"] is True


def test_the_key_validator_does_not_ask_the_mcp_server(monkeypatch):
    """`tools/list` returns all 25 tools for ANY Authorization header, valid or not
    (verified 2026-09-07 with a made-up key), so it cannot tell a good key from a bad
    one — only a paid tool call can. Credit-usage is a free read that answers 401.
    """
    import harness.integrations.connectors.descriptors as d
    import harness.integrations.connectors.descriptor.validators as validators

    calls: list[str] = []

    class _R:
        status_code = 200

        def json(self):
            return {}

    monkeypatch.setattr(
        httpx, "request", lambda method, url, **kw: (calls.append(url), _R())[1]
    )
    res = validators.validate_firecrawl({"api_key": "fc-abcdef123456"})

    assert res.ok and res.identity.endswith("123456")
    assert calls == ["https://api.firecrawl.dev/v2/team/credit-usage"]


def test_a_rejected_key_does_not_connect(monkeypatch):
    import harness.integrations.connectors.descriptors as d
    import harness.integrations.connectors.descriptor.validators as validators

    class _R:
        status_code = 401

        def json(self):
            return {"error": "Unauthorized: Invalid token"}

    monkeypatch.setattr(httpx, "request", lambda method, url, **kw: _R())
    res = validators.validate_firecrawl({"api_key": "fc-bad"})
    assert not res.ok and "Unauthorized" in res.error


def test_no_oauth_machinery_survives_for_this_connector():
    """The browser path was removed on 2026-09-07: it never completed a sign-in once, the
    cause was never found, and it reached the same 25 tools the key reaches. What is left
    must not quietly carry half of it — a descriptor still naming OAuth endpoints would
    read as though a browser flow exists."""
    import pathlib

    from harness.integrations.connectors.descriptors import get_descriptor

    fc = get_descriptor(NAME)
    assert fc.mcp_api_key_auth is True
    assert not fc.pkce_auth and not fc.mcp_device_auth
    assert not fc.pkce_auth_endpoint and not fc.oauth_token_endpoint
    assert not fc.oauth_scope and not fc.oauth_client_id
    assert not hasattr(fc, "oauth_register_endpoint")

    root = pathlib.Path(__file__).resolve().parents[1]
    assert not (root / "harness/extensions/mcp/dcr_pkce.py").exists()


def test_an_explicit_web_search_key_still_wins(tmp_path, monkeypatch):
    """The same-named connector secret is a FALLBACK. Someone who deliberately set a
    different key on `web_search:default` — a separate quota, say — must keep it."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from harness.integrations.web.providers import ExaProvider
    from harness.integrations.web.tool import resolve_provider

    store = SecretStore(tmp_path / "secrets.json")
    store.put("exa:default", {"api_key": "exa-connector"})
    store.put("web_search:default", {"provider": "exa", "api_key": "exa-explicit"})
    provider = resolve_provider(store)
    assert isinstance(provider.primary, ExaProvider)
    assert provider.primary.api_key == "exa-explicit"
