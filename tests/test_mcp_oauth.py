"""Browser sign-in for remote MCP servers (`extensions/mcp/oauth`): which stored registration is
still good, where tokens are kept and how old they are, how the browser's redirect reaches the flow
waiting for it, and what the server list says about each one.

The OAuth flow itself belongs to the MCP SDK; these pin the places Reeve meets it. No OAuth server
is reached.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from harness.extensions.mcp import oauth as mcp_oauth
from harness.extensions.mcp.config import load_mcp_servers
from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.safety.secrets import SecretStore

SIGNS_IN = {"type": "http", "url": "https://mcp.example.com/mcp", "auth": "oauth"}
ATLASSIAN = "https://mcp.atlassian.com/v2/mcp"


def _redirect(port: int) -> list[str]:
    return [f"http://127.0.0.1:{port}/mcp/oauth/callback"]


@pytest.fixture(autouse=True)
def _no_flow_waiting():
    def clear():
        mcp_oauth.FLOW.pending = None
        mcp_oauth.FLOW.expected_state = None

    clear()
    yield
    clear()


def _state_dir(tmp_path, monkeypatch, servers=None):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mcp_json = tmp_path / "state" / "mcp.json"
    mcp_json.parent.mkdir(parents=True, exist_ok=True)
    mcp_json.write_text(json.dumps({"mcpServers": servers or {}}), encoding="utf-8")


def _stored(secrets, name):
    return secrets.get("mcp-oauth:" + name) or {}


# -- which registration is kept ------------------------------------------------------------------
#
# A client id from dynamic registration is issued by one authorization server for one redirect.
# Carried to another endpoint it is "unknown" (Atlassian's v1 -> v2 move, owner-hit 2026-09-05: the
# page only says "we couldn't identify the app requesting access"); carried to a port nothing
# listens on any more it is refused at the authorize step ("The app's callback URL is invalid.
# Contact the app developer", owner-hit 2026-09-07: jira had :65531 stored, monday :50093, the app
# ran on :8765). Neither clears by retrying, so such a registration goes, but a working connection
# is never signed out over a guess.


@pytest.mark.parametrize(
    ("name", "profile", "url", "port", "dropped"),
    [
        pytest.param("monday", {"client_info": {"client_id": "c"}, "registered_for": "https://mcp.monday.com/mcp", "tokens": {"access_token": "a"}}, "https://mcp.monday.com/mcp", None, False, id="same endpoint"),
        # written before the stamp existed: absence is ambiguous and the tokens work, so it is stamped
        pytest.param("notes", {"client_info": {"client_id": "c"}, "tokens": {"access_token": "live"}}, "https://x/mcp", None, False, id="unstamped with tokens"),
        # the state a failed connect leaves, the one that keeps failing: nothing to lose
        pytest.param("jira", {"client_info": {"client_id": "half-done"}}, "https://new/mcp", None, True, id="unstamped without tokens"),
        # same endpoint, so only the port can tell
        pytest.param("jira", {"client_info": {"client_id": "c", "redirect_uris": _redirect(65531)}, "registered_for": ATLASSIAN}, ATLASSIAN, "8765", True, id="port moved"),
        pytest.param("jira", {"client_info": {"client_id": "c", "redirect_uris": _redirect(8765)}, "registered_for": ATLASSIAN}, ATLASSIAN, "8765", False, id="port served"),
        # a refresh carries no redirect_uri, so the moved port is not their problem yet
        pytest.param("jira", {"client_info": {"client_id": "c", "redirect_uris": _redirect(65531)}, "registered_for": ATLASSIAN, "tokens": {"access_token": "live", "refresh_token": "r"}}, ATLASSIAN, "8765", False, id="port moved, live token"),
        # older profiles kept no redirect_uris; a mismatch is not invented from that
        pytest.param("monday", {"client_info": {"client_id": "c"}, "registered_for": "https://mcp.monday.com/mcp"}, "https://mcp.monday.com/mcp", "8765", False, id="no redirect recorded"),
    ],
)
def test_a_registration_goes_only_when_it_cannot_work_and_nothing_working_rests_on_it(tmp_path, monkeypatch, name, profile, url, port, dropped):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    if port:
        monkeypatch.setenv("REEVE_PORT", port)
    secrets = SecretStore()
    secrets.put("mcp-oauth:" + name, profile)
    assert mcp_oauth.invalidate_stale_registration(name, url, secrets) is dropped
    left = _stored(secrets, name)
    if dropped:
        assert "client_info" not in left
    else:
        assert left["client_info"]["client_id"] == profile["client_info"]["client_id"]
        assert left.get("tokens") == profile.get("tokens")
        assert left["registered_for"] == url  # an unstamped one is stamped for next time


def test_a_registration_from_another_endpoint_goes_with_everything_it_minted(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    secrets = SecretStore()
    secrets.put("mcp-oauth:jira", {
        "client_info": {"client_id": "issued-by-v1"},
        "registered_for": "https://mcp.atlassian.com/v1/mcp",
        "tokens": {"access_token": "a", "token_type": "bearer"},
        "oauth_metadata": {"issuer": "https://old"},
    })
    assert mcp_oauth.invalidate_stale_registration("jira", "https://mcp.atlassian.com/v2/mcp?tools=all", secrets) is True
    left = _stored(secrets, "jira")
    assert "client_info" not in left and "tokens" not in left and "oauth_metadata" not in left
    assert left["registered_for"] == "https://mcp.atlassian.com/v2/mcp?tools=all"


# -- the config and the token store --------------------------------------------------------------


def test_a_server_that_signs_in_says_so_in_its_config(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch, {"notes": SIGNS_IN, "plain": {"url": "https://x/mcp"}})
    servers = {s.name: s for s in load_mcp_servers()}
    assert (servers["notes"].auth, servers["notes"].transport) == ("oauth", "http")
    assert servers["plain"].auth is None


def test_tokens_and_the_registration_survive_a_restart_until_sign_out(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch)
    secrets = SecretStore()
    storage = mcp_oauth.SecretStoreTokenStorage("notes", secrets)

    async def keep_and_read():
        assert await storage.get_tokens() is None
        await storage.set_tokens(OAuthToken.model_validate({"access_token": "at", "token_type": "Bearer", "refresh_token": "rt"}))
        await storage.set_client_info(OAuthClientInformationFull.model_validate({"client_id": "dcr-123", "redirect_uris": _redirect(8765)}))
        return await storage.get_tokens(), await storage.get_client_info()

    tokens, info = asyncio.run(keep_and_read())
    assert (tokens.access_token, tokens.refresh_token, info.client_id) == ("at", "rt", "dcr-123")
    assert mcp_oauth.has_tokens("notes", secrets)
    assert mcp_oauth.sign_out("notes", secrets)
    assert not mcp_oauth.has_tokens("notes", secrets)


# The SDK computes no expiry for tokens it loads and takes a missing one as valid for ever, so a
# stale bearer was sent (owner-hit 2026-08-21). The store reports what is left of the lifetime from
# the time it kept, and blanks a stale access token so the SDK refreshes before the request.


async def test_a_kept_token_reports_what_is_left_of_its_life_and_is_blanked_once_spent(tmp_path, monkeypatch):
    storage = mcp_oauth.SecretStoreTokenStorage("acme", SecretStore(tmp_path / "s.json"))
    issued = 1_000_000.0
    monkeypatch.setattr(mcp_oauth.storage.time, "time", lambda: issued)
    await storage.set_tokens(OAuthToken(access_token="A", expires_in=3600, refresh_token="R"))
    for elapsed, left in ((0, 3600), (1800, 1800)):
        monkeypatch.setattr(mcp_oauth.storage.time, "time", lambda elapsed=elapsed: issued + elapsed)
        token = await storage.get_tokens()
        assert (token.access_token, token.expires_in) == ("A", left)
    monkeypatch.setattr(mcp_oauth.storage.time, "time", lambda: issued + 3700)
    spent = await storage.get_tokens()
    assert (spent.access_token, spent.refresh_token) == ("", "R") and spent.expires_in <= 0


async def test_a_token_of_unknown_age_is_taken_as_spent(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("mcp-oauth:acme", {"tokens": {"access_token": "A", "expires_in": 3600, "refresh_token": "R"}})  # kept before its time was
    token = await mcp_oauth.SecretStoreTokenStorage("acme", secrets).get_tokens()
    assert (token.access_token, token.refresh_token) == ("", "R")


async def test_the_kept_server_metadata_is_there_before_the_first_refresh(tmp_path):
    """The refresh runs before the SDK's discovery; without the kept metadata it posts to
    <origin>/token, which is a 404 on some servers."""
    secrets = SecretStore(tmp_path / "s.json")
    metadata = {"issuer": "https://data.example", "authorization_endpoint": "https://data.example/api/auth/authorize", "token_endpoint": "https://data.example/api/auth/token"}
    secrets.put("mcp-oauth:acme", {"oauth_metadata": metadata})
    auth = mcp_oauth.build_auth("acme", "https://data.example/api/mcp", secrets, interactive=False)
    await auth._initialize()
    assert str(auth.context.oauth_metadata.token_endpoint) == metadata["token_endpoint"]


# -- the browser's redirect ----------------------------------------------------------------------


def test_a_redirect_with_no_flow_waiting_is_refused():
    assert mcp_oauth.deliver_callback("code", "state") is False


def test_the_waiting_flow_takes_the_redirect_with_its_own_state_and_no_other():
    async def run(expected):
        waiting = asyncio.create_task(mcp_oauth.FLOW.wait_for_callback())
        await asyncio.sleep(0)  # the waiter puts up its future
        if expected:
            mcp_oauth.FLOW.expected_state = expected  # read from the authorize URL
            # a stray or forged hit is ignored and the flow still waits for the real one
            assert mcp_oauth.deliver_callback("evil", "bad-state") is False
            assert mcp_oauth.deliver_callback("evil", None) is False
            assert not waiting.done()
        assert mcp_oauth.deliver_callback("c0de", expected or "st4te") is True
        return await waiting

    assert asyncio.run(run(None)) == ("c0de", "st4te")
    assert asyncio.run(run("good-state")) == ("c0de", "good-state")


def test_the_state_is_read_from_the_authorize_url():
    assert mcp_oauth.flow._state_of("https://idp.example/authorize?client_id=x&state=abc123&scope=y") == "abc123"
    assert mcp_oauth.flow._state_of("https://idp.example/authorize?client_id=x") is None


def test_the_redirect_page_says_what_became_of_the_sign_in(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch)
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    refused = client.get("/mcp/oauth/callback", params={"error": "access_denied"})
    assert refused.status_code == 400 and "failed" in refused.text.lower()
    stale_tab = client.get("/mcp/oauth/callback", params={"code": "x"})
    assert stale_tab.status_code == 400 and "waiting" in stale_tab.text.lower()
    loop = asyncio.new_event_loop()
    try:
        mcp_oauth.FLOW.pending = waiting = loop.create_future()
        done = client.get("/mcp/oauth/callback", params={"code": "c1", "state": "s1"})
        assert done.status_code == 200 and "close this tab" in done.text.lower()
        assert waiting.result() == ("c1", "s1")
    finally:
        loop.close()


# -- what the server list says -------------------------------------------------------------------


def test_the_server_list_follows_a_sign_in_from_needed_to_signed_out(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch, {"notes": SIGNS_IN})
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))

    def row():
        return client.get("/v1/mcp").json()["servers"][0]

    assert (row()["auth"], row()["status"]) == ("oauth", "needs_auth")
    manager._mcp_authorizing.add("notes")
    assert row()["status"] == "authorizing"
    manager._mcp_authorizing.discard("notes")
    manager._mcp_errors["notes"] = "sign-in timed out"
    assert row()["last_error"] == "sign-in timed out"
    manager.secrets.put("mcp-oauth:notes", {"tokens": {"access_token": "at"}})
    assert row()["status"] == "configured"
    assert client.post("/v1/mcp/notes/signout").json()["ok"] is True
    assert not mcp_oauth.has_tokens("notes", manager.secrets)
    assert row()["status"] == "needs_auth"


def test_connect_starts_the_sign_in_and_answers_at_once(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch, {"notes": SIGNS_IN})
    manager = SessionManager(data_dir=tmp_path / "data")
    started: list[str] = []

    async def connect(name):
        started.append(name)
        return {"ok": True}

    monkeypatch.setattr(manager, "connect_mcp", connect)
    assert TestClient(create_app(manager)).post("/v1/mcp/notes/connect").json() == {"ok": True, "started": True}
    assert started == ["notes"]
