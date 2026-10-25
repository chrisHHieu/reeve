"""Postman: one vendor MCP server behind three endpoints, and the user picks.

Probed live 2026-09-08 rather than read off the docs — every endpoint answers 401 with
RFC 9728 `resource_metadata`, the authorization server advertises DCR + PKCE S256 + a
public client, and an anonymous registration returned 201. So the sign-in is the same
one-click shape as Atlassian and Notion; what is new is that the SAME connector can point
at different endpoints.

Counted from the vendor's own `src/enabledResources.ts`, not from their "100+" blurb:

    /minimal   42 listed / 41 served   19 write   0 delete
    /mcp      140 listed               76 write  17 delete
    /code      23 listed /  23 served   0 write   0 delete

That last column is why Essentials is the default, and why the choice is offered at all.

The card quotes the SERVED number, measured on a live connection 2026-09-08 — Essentials
lists 42 and serves 41, because one entry in their source is an orchestrator that folds
subtools. Full is not wire-measured: connecting to it purely to count would grant 17
delete tools over a real account nobody asked to hand over.

The load-bearing test here is `test_the_session_plan_resolves_the_users_choice`. The plan
deliberately rewrites `server.url` from the descriptor on every session build, so that a
vendor moving an endpoint reaches installs that connected before the move. A variant
written into mcp.json would be erased by exactly that; a variant key in the PROFILE
survives it. Get this wrong and the symptom is silent — the user picks Full, signs in to
Full, and every session afterwards quietly talks to Essentials.
"""

from __future__ import annotations

import asyncio

from harness.integrations.connectors.descriptors import get_descriptor, list_descriptors, mcp_url_for, mcp_variant_for
from harness.integrations.connectors.lifecycle import connector_list
from harness.extensions.mcp.config import read_global
from harness.safety.secrets import SecretStore
from harness.interface.server.manager import SessionManager


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)


def test_postman_is_an_mcp_backed_one_click_connector():
    d = get_descriptor("postman")
    assert d is not None and d.available
    assert d.mcp_url.startswith("https://mcp.postman.com/")
    assert d.auth == "oauth"
    # No pasted key, so no manual field: the whole connect is the browser flow.
    assert d.fields == []
    assert not d.mcp_api_key_auth, "an api-key connector would lose its one-click button"


def test_the_default_is_the_endpoint_that_cannot_delete_anything():
    """Essentials is not "Full but smaller" — it is the set with no destructive tool.
    An agent running multi-step turns on someone's real API workspace should not have
    deleteWorkspace within reach unless they asked for it."""
    d = get_descriptor("postman")
    assert d.mcp_variant_default == "minimal"
    assert mcp_url_for(d, {}) == "https://mcp.postman.com/minimal"
    assert d.mcp_variants[0].key == "minimal", "the default is offered first"
    # and the card says which one deletes, because that is the actual decision
    full = next(v for v in d.mcp_variants if v.key == "full")
    assert "delet" in full.summary.lower()


def test_learn_is_not_one_of_the_choices():
    """`/learn` is one tool (documentation search) behind its own OAuth resource, so
    bundling it would cost a second browser sign-in for a single tool — and it already
    sits inside the full set for anyone who wants it."""
    d = get_descriptor("postman")
    keys = {v.key for v in d.mcp_variants}
    assert keys == {"minimal", "full", "code"}
    assert not any("learn" in v.url for v in d.mcp_variants)


def test_each_variant_resolves_to_its_own_endpoint():
    d = get_descriptor("postman")
    assert mcp_url_for(d, {"mcp_variant": "full"}) == "https://mcp.postman.com/mcp"
    assert mcp_url_for(d, {"mcp_variant": "code"}) == "https://mcp.postman.com/code"
    assert mcp_url_for(d, {"mcp_variant": "minimal"}) == "https://mcp.postman.com/minimal"
    # The three are genuinely different endpoints, not three names for one url.
    assert len({v.url for v in d.mcp_variants}) == 3


def test_a_key_that_no_longer_exists_falls_back_instead_of_breaking():
    """A retired variant, or a hand-edited profile, must not resolve to an empty url —
    that is a connector that fails to connect with nothing on screen to explain it."""
    d = get_descriptor("postman")
    assert mcp_url_for(d, {"mcp_variant": "learn"}) == d.mcp_url
    assert mcp_url_for(d, {"mcp_variant": ""}) == d.mcp_url
    assert mcp_url_for(d, None) == d.mcp_url
    assert mcp_variant_for(d, {"mcp_variant": "nope"}) == "minimal"


def test_single_endpoint_connectors_are_untouched():
    """Every other MCP connector has no variants, and a stray key in its profile must
    not change where it points."""
    for name in ("upwork", "notion", "tcbs"):
        d = get_descriptor(name)
        assert d.mcp_variants == ()
        assert mcp_url_for(d, {"mcp_variant": "full"}) == d.mcp_url
        assert mcp_variant_for(d, {}) == ""


def test_connect_seeds_the_endpoint_the_user_picked(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def fake_connect(name):
        return {"ok": True, "tools": 140}

    monkeypatch.setattr(manager, "connect_mcp", fake_connect)
    out = asyncio.run(manager.mcp_connect_connector("postman", "full"))
    assert out["ok"]

    raw = read_global()["postman"]
    assert raw["url"] == "https://mcp.postman.com/mcp"
    assert raw["auth"] == "oauth"
    # The choice is remembered on the profile, which is what every later resolution reads.
    assert manager.secrets.get("postman:default")["mcp_variant"] == "full"
    assert manager.secrets.get("postman:default")["mode"] == "mcp"


def test_connect_without_a_choice_takes_the_default(tmp_path, monkeypatch):
    """Every other connector posts no body at all; the parameter has to stay optional."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def fake_connect(name):
        return {"ok": True, "tools": 42}

    monkeypatch.setattr(manager, "connect_mcp", fake_connect)
    assert asyncio.run(manager.mcp_connect_connector("postman"))["ok"]
    assert read_global()["postman"]["url"] == "https://mcp.postman.com/minimal"


def test_the_session_plan_resolves_the_users_choice(tmp_path, monkeypatch):
    """The one that matters. `_mcp_plan` overwrites the seeded url from the descriptor
    every build — on purpose, so a moved endpoint reaches old installs. It must resolve
    through the profile's variant, or the user signs in to Full and then silently talks
    to Essentials for the rest of the install's life."""
    _state(tmp_path, monkeypatch)
    monkeypatch.setenv("REEVE_HOME", str(tmp_path))
    from harness.extensions.mcp.config import put_global_server

    put_global_server(
        "postman",
        {"url": "https://mcp.postman.com/minimal", "auth": "oauth", "enabled": True},
    )
    secrets = SecretStore()
    secrets.put("postman:default", {"mode": "mcp", "mcp_variant": "full"})

    mgr = SessionManager(workspace=str(tmp_path))
    mgr.effective_connectors = lambda *a, **k: {"postman"}  # type: ignore[method-assign]
    monkeypatch.setattr("harness.extensions.mcp.oauth.has_tokens", lambda name, secrets: True)

    plan = {s.name: s for s in mgr._mcp_plan("sess", None, "code")}
    assert plan["postman"].url == "https://mcp.postman.com/mcp"


def test_switching_endpoints_discards_the_registration_bound_to_the_old_one(
    tmp_path, monkeypatch
):
    """Each endpoint is its own OAuth resource, so a client id and tokens issued for
    Essentials are worthless against Full. The guard written for Atlassian's v1 -> v2 move
    covers this for free — but only because the url actually changes, which is the thing
    worth pinning."""
    _state(tmp_path, monkeypatch)
    from harness.extensions.mcp.oauth import invalidate_stale_registration

    secrets = SecretStore()
    secrets.put(
        "mcp-oauth:postman",
        {
            "client_info": {"client_id": "abc", "redirect_uris": ["http://x/cb"]},
            "tokens": {"access_token": "t"},
            "registered_for": "https://mcp.postman.com/minimal",
        },
    )
    dropped = invalidate_stale_registration(
        "postman", "https://mcp.postman.com/mcp", secrets
    )
    assert dropped, "a registration issued for /minimal cannot be reused against /mcp"
    after = secrets.get("mcp-oauth:postman")
    assert "tokens" not in after and "client_info" not in after
    assert after["registered_for"] == "https://mcp.postman.com/mcp"

    # Reconnecting to the SAME endpoint must not throw a working connection away.
    secrets.put(
        "mcp-oauth:postman",
        {
            "client_info": {"client_id": "abc", "redirect_uris": ["http://x/cb"]},
            "tokens": {"access_token": "t"},
            "registered_for": "https://mcp.postman.com/mcp",
        },
    )
    monkeypatch.setattr(
        "harness.extensions.mcp.oauth.registration.redirect_is_stale", lambda profile: False
    )
    assert not invalidate_stale_registration(
        "postman", "https://mcp.postman.com/mcp", secrets
    )


def test_the_card_offers_the_choice_and_keeps_the_urls_to_itself(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    card = next(c for c in connector_list(secrets) if c["name"] == "postman")

    assert card["one_click"] is True
    assert [v["key"] for v in card["mcp_variants"]] == ["minimal", "full", "code"]
    assert card["mcp_variant"] == "minimal", "unconnected card shows the default"
    assert all(v["summary"] for v in card["mcp_variants"]), "each option says what it costs"
    # Endpoints are the app's decision, not the client's: the GUI posts back a KEY, so a
    # url on the wire would be a second source of truth waiting to disagree.
    assert not any("url" in v for v in card["mcp_variants"])
    assert "postman.com" not in str(card["mcp_variants"])

    secrets.put("postman:default", {"mode": "mcp", "mcp_variant": "code"})
    card = next(c for c in connector_list(secrets) if c["name"] == "postman")
    assert card["mcp_variant"] == "code", "a connected card shows what it is on"


def test_every_connector_card_carries_the_new_keys(tmp_path, monkeypatch):
    """The GUI reads these on every card, not just Postman's."""
    _state(tmp_path, monkeypatch)
    cards = connector_list(SecretStore())
    assert cards
    for c in cards:
        assert isinstance(c["mcp_variants"], list)
        assert isinstance(c["mcp_variant"], str)
    # exactly one connector uses them today; if a second appears, that is a decision
    # someone made deliberately and this test should be updated with it
    assert [c["name"] for c in cards if c["mcp_variants"]] == ["postman"]


def test_postman_ships_no_hand_written_tools():
    """MCP-backed connectors take the vendor's catalogue; a static tool set here would
    be a second, drifting answer to what Postman can do."""
    from harness.integrations.connectors.catalog import tool_dicts

    _ = list_descriptors()
    assert tool_dicts(SecretStore(), "postman") == []


def test_a_failed_one_click_has_somewhere_to_appear(tmp_path, monkeypatch):
    """`list_mcp` hides connector-backed servers from the MCP tab, which left a failed
    sign-in with nowhere at all to surface: the dialog said "Check your browser…" and
    polled `connected` forever while the reason sat unread in `_mcp_errors` (owner-hit
    2026-09-08, Postman — no browser ever opened)."""
    _state(tmp_path, monkeypatch)
    mgr = SessionManager(data_dir=tmp_path / "data")
    mgr._mcp_errors["postman"] = "DCR rejected the redirect"

    card = next(c for c in mgr.list_connectors() if c["name"] == "postman")
    assert card["mcp_error"] == "DCR rejected the redirect"
    assert card["mcp_authorizing"] is False

    # While a flow is genuinely in flight, the dialog must keep waiting rather than
    # abort on an error left over from an earlier attempt.
    mgr._mcp_authorizing.add("postman")
    card = next(c for c in mgr.list_connectors() if c["name"] == "postman")
    assert card["mcp_authorizing"] is True

    # A connector that never failed carries no error at all — an empty string here would
    # read as a failure to any caller checking truthiness.
    clean = next(c for c in mgr.list_connectors() if c["name"] == "upwork")
    assert "mcp_error" not in clean


def test_the_card_stays_grey_until_there_are_tokens(tmp_path, monkeypatch):
    """The endpoint choice lives in the profile, and a connector with no manual fields
    has nothing else in there — so `_profile_connected` would check an empty list of
    required fields, find `all([])` true, and call any non-empty profile connected. The
    card went green the moment a sign-in STARTED, with no tokens behind it (2026-09-08,
    caught by driving the endpoint; no test had asked)."""
    _state(tmp_path, monkeypatch)
    secrets = SecretStore()
    secrets.put("postman:default", {"mcp_variant": "code"})

    card = next(c for c in connector_list(secrets) if c["name"] == "postman")
    assert card["connected"] is False, "a stored preference is not a connection"

    monkeypatch.setattr(
        "harness.integrations.connectors.lifecycle.listing._mcp_tokens_present", lambda s, n: True
    )
    card = next(c for c in connector_list(secrets) if c["name"] == "postman")
    assert card["connected"] is True, "tokens are what connected means here"


def test_a_failed_connect_leaves_nothing_behind(tmp_path, monkeypatch):
    """Same rule as the seeded server config: a one-click that fails must not leave a
    profile either. The choice is persisted only once the sign-in has actually landed."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def fail_connect(name):
        return {"ok": False, "error": "user closed the browser"}

    monkeypatch.setattr(manager, "connect_mcp", fail_connect)
    out = asyncio.run(manager.mcp_connect_connector("postman", "full"))
    assert not out["ok"]
    assert read_global().get("postman") is None
    assert manager.secrets.get("postman:default") is None

    # …and the card is not claiming a connection off the back of it.
    card = next(c for c in connector_list(manager.secrets) if c["name"] == "postman")
    assert card["connected"] is False


def test_the_card_quotes_what_the_server_actually_serves():
    """Measured on a live connection 2026-09-08: /minimal returned 41 tools and /code
    returned 23. The vendor's source lists 42 for minimal — one of its entries is an
    orchestrator that folds subtools — so quoting the source would put a number on the
    card that the user's own tool list contradicts."""
    d = get_descriptor("postman")
    by_key = {v.key: v.summary for v in d.mcp_variants}
    assert "41 tools" in by_key["minimal"]
    assert "23 tools" in by_key["code"]
    assert "42" not in by_key["minimal"], "42 is the listed count, not the served one"
