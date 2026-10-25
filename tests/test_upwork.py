"""Upwork: the vendor's own remote MCP server, one endpoint, browser sign-in.

Probed live 2026-09-10 rather than read off their page, which answers 403 to anything that
is not a browser:

    POST https://mcp.upwork.com/mcp           -> 401 + RFC 9728 `resource_metadata`
    /.well-known/oauth-protected-resource/mcp -> resource + authorization_servers
    /.well-known/oauth-authorization-server   -> PKCE S256, token_endpoint_auth "none",
                                                 registration_endpoint
    POST that registration_endpoint           -> 201 with a client_id, no secret

The last step is the one that mattered. Upwork advertises its registration endpoint as
`https://www.upwork.com/register`, which reads exactly like a human signup page — and
Asana already taught this codebase that a vendor can publish DCR metadata its server does
not honour. Upwork honours it. Checking beat assuming, in the direction of good news.

Note the authorization and token endpoints sit on `www.upwork.com`, not on the MCP host.
That is legal — the metadata says where they are — and it is why discovery has to be
followed rather than inferred from the server URL.
"""

from __future__ import annotations

import asyncio

from harness.integrations.connectors.descriptors import get_descriptor, mcp_url_for
from harness.integrations.connectors.lifecycle import connector_list
from harness.extensions.mcp.config import read_global
from harness.safety.secrets import SecretStore
from harness.interface.server.manager import SessionManager


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)


def test_upwork_is_a_one_click_mcp_backed_connector():
    d = get_descriptor("upwork")
    assert d is not None and d.available
    assert d.mcp_url == "https://mcp.upwork.com/mcp"
    assert d.auth == "oauth"
    # Browser sign-in only: no pasted key, so no manual field to fill in.
    assert d.fields == []
    assert not d.mcp_api_key_auth
    # One endpoint — the variant machinery is Postman's, and must not leak here.
    assert d.mcp_variants == ()


def test_the_card_says_what_the_agent_can_spend_and_commit():
    """Two of these tools are not drafts. Boosting a proposal spends Connects, which cost
    real money, and responding to an offer is a contract decision. A card that lists
    "manage proposals" without saying that is telling half the truth."""
    from harness.integrations.connectors.catalog_copy import access_for

    bullets = " ".join(access_for("upwork")).lower()
    assert "connects" in bullets and "money" in bullets
    assert "offer" in bullets


def test_connect_seeds_the_vendors_endpoint(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def fake_connect(name):
        return {"ok": True, "tools": 29}

    monkeypatch.setattr(manager, "connect_mcp", fake_connect)
    assert asyncio.run(manager.mcp_connect_connector("upwork"))["ok"]

    raw = read_global()["upwork"]
    assert raw["url"] == "https://mcp.upwork.com/mcp"
    assert raw["auth"] == "oauth"
    # Nothing narrows the vendor's catalogue — the owner call of 2026-09-05 stands.
    assert "include_tools" not in raw
    assert manager.secrets.get("upwork:default")["mode"] == "mcp"


def test_a_failed_connect_leaves_nothing_behind(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def fail_connect(name):
        return {"ok": False, "error": "user closed the browser"}

    monkeypatch.setattr(manager, "connect_mcp", fail_connect)
    assert not asyncio.run(manager.mcp_connect_connector("upwork"))["ok"]
    assert read_global().get("upwork") is None
    assert manager.secrets.get("upwork:default") is None


def test_the_card_is_grey_until_there_are_tokens(tmp_path, monkeypatch):
    """Upwork has no manual fields, so its connectedness is its tokens and nothing else —
    the trap that turned the Postman card green mid-sign-in (2026-09-08)."""
    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    secrets.put("upwork:default", {"mode": "mcp"})

    card = next(c for c in connector_list(secrets) if c["name"] == "upwork")
    assert card["connected"] is False
    assert card["one_click"] is True
    assert card["mcp_variants"] == [], "one endpoint means no choice to offer"

    monkeypatch.setattr(
        "harness.integrations.connectors.lifecycle.listing._mcp_tokens_present", lambda s, n: True
    )
    card = next(c for c in connector_list(secrets) if c["name"] == "upwork")
    assert card["connected"] is True


def test_a_stray_variant_key_cannot_move_the_endpoint():
    """Postman's picker writes `mcp_variant` into a profile. A hand-edited or copied
    profile must not be able to point a single-endpoint connector somewhere else."""
    d = get_descriptor("upwork")
    assert mcp_url_for(d, {"mcp_variant": "full"}) == "https://mcp.upwork.com/mcp"


def test_upwork_ships_no_hand_written_tools():
    from harness.integrations.connectors.catalog import tool_dicts

    assert tool_dicts(SecretStore(), "upwork") == []
