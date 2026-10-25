"""Google Workspace sign-in storage and refresh — `connectors/google_auth.py`.

These guard three failures that are SILENT in production:

  - a token stored where the connector's readers do not look, which shows the card as
    "not connected" while holding a perfectly good token (we shipped that once, for
    Notion, and it also cut the connector from every session);
  - a refresh response that omits `refresh_token` overwriting the stored one, ending the
    connection at the FOLLOWING expiry with no way back;
  - no refresh at all, which works beautifully for an hour after sign-in and then fails
    every call for the rest of the day.

No live Google here — httpx MockTransport stands in.
"""

from __future__ import annotations

import time

import httpx

from harness.integrations.connectors import google_auth as ga
from harness.safety.secrets import SecretStore


def _mock(monkeypatch, handler):
    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", factory)

    def one_shot(url, **kw):
        with factory(timeout=5) as c:
            return c.get(url, **kw)

    monkeypatch.setattr(httpx, "get", one_shot)

    def posted(url, **kw):
        with factory(timeout=5) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(httpx, "post", posted)


def _identity(email="me@example.com"):
    def handler(request):
        if "userinfo" in str(request.url):
            return httpx.Response(200, json={"email": email})
        return httpx.Response(404, json={})

    return handler


# ------------------------------------------------------------------ where tokens land


def test_a_plain_connector_stores_the_token_where_its_tools_read_it(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _mock(monkeypatch, _identity())
    result = ga.store_signin(
        secrets,
        "google_docs",
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3599},
        client_id="cid",
        client_secret="sec",
    )
    assert result["ok"] is True
    # `integration_tools._profile` reads exactly this key and this field.
    profile = secrets.get("google_docs:default")
    assert profile["access_token"] == "at"
    assert profile["refresh_token"] == "rt"
    assert profile["expires_at"] > time.time()
    assert profile["google_client_id"] == "cid"


def test_an_account_connector_stores_an_account_row_so_the_card_reads_connected(
    tmp_path, monkeypatch
):
    """setup.py derives `connected` for google_calendar from its ACCOUNT ROWS.

    A sign-in that writes anywhere else leaves the card disconnected while holding a
    working token — and `enabled` goes false with it, cutting the connector from every
    session. That is the Notion bug of 2026-09-05, and this is the guard against it.
    """
    from harness.integrations.connectors import gcal_accounts

    secrets = SecretStore(tmp_path / "s.json")
    _mock(monkeypatch, _identity("owner@example.com"))
    result = ga.store_signin(
        secrets,
        "google_calendar",
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3599},
        client_id="cid",
        client_secret="sec",
    )
    assert result["ok"] is True
    rows = gcal_accounts.list_accounts(secrets)
    assert [email for email, _ in rows] == ["owner@example.com"]
    assert rows[0][1]["access_token"] == "at"


def test_a_sign_in_with_no_readable_email_fails_rather_than_filing_it_nowhere(
    tmp_path, monkeypatch
):
    secrets = SecretStore(tmp_path / "s.json")
    _mock(monkeypatch, lambda request: httpx.Response(403, json={}))
    result = ga.store_signin(
        secrets,
        "google_calendar",
        {"access_token": "at"},
        client_id="cid",
        client_secret="sec",
    )
    assert result["ok"] is False
    assert "email" in result["error"]


# ------------------------------------------------------------------ refresh


def _profile(**over):
    base = {
        "access_token": "old",
        "refresh_token": "rt",
        "google_client_id": "cid",
        "google_client_secret": "sec",
        "expires_at": int(time.time()) + 3600,
    }
    base.update(over)
    return base


def test_a_live_token_is_used_without_a_network_call(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")

    def handler(request):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("refreshed a token that had not expired")

    _mock(monkeypatch, handler)
    token, err = ga.fresh_token(secrets, "google_docs", "google_docs:default", _profile())
    assert (token, err) == ("old", None)


def test_an_expired_token_is_refreshed_in_place(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    key = "google_docs:default"
    profile = _profile(expires_at=int(time.time()) - 60)
    secrets.put(key, profile)

    seen = {}

    def handler(request):
        from urllib.parse import parse_qs

        seen["body"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    _mock(monkeypatch, handler)
    token, err = ga.fresh_token(secrets, "google_docs", key, profile)

    assert (token, err) == ("new", None)
    assert seen["body"]["grant_type"] == ["refresh_token"]
    assert seen["body"]["refresh_token"] == ["rt"]
    # and it is persisted, or the next call refreshes all over again
    assert secrets.get(key)["access_token"] == "new"
    assert secrets.get(key)["expires_at"] > time.time()


def test_a_refresh_that_omits_the_refresh_token_keeps_the_old_one(tmp_path, monkeypatch):
    """Google's refresh response carries no refresh_token. Overwriting blindly would drop
    the only way back, and the connection would end at the next expiry."""
    secrets = SecretStore(tmp_path / "s.json")
    key = "google_docs:default"
    profile = _profile(expires_at=int(time.time()) - 60)
    secrets.put(key, profile)

    _mock(monkeypatch, lambda r: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))
    ga.fresh_token(secrets, "google_docs", key, profile)
    assert secrets.get(key)["refresh_token"] == "rt"


def test_a_refused_refresh_reports_it_instead_of_sending_a_dead_token(
    tmp_path, monkeypatch
):
    secrets = SecretStore(tmp_path / "s.json")
    key = "google_docs:default"
    profile = _profile(expires_at=int(time.time()) - 60)
    secrets.put(key, profile)

    _mock(monkeypatch, lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    token, err = ga.fresh_token(secrets, "google_docs", key, profile)
    assert token == ""
    assert err and "connect it again" in err["error"]


def test_a_hand_pasted_token_is_left_alone(tmp_path, monkeypatch):
    """The old flow's profiles carry no expiry and no client. They must keep working
    exactly as before — used as-is, failing at the API when they die."""
    secrets = SecretStore(tmp_path / "s.json")

    def handler(request):  # pragma: no cover
        raise AssertionError("tried to refresh a pasted token")

    _mock(monkeypatch, handler)
    token, err = ga.fresh_token(
        secrets, "google_docs", "google_docs:default", {"access_token": "pasted"}
    )
    assert (token, err) == ("pasted", None)


def test_an_empty_profile_reads_as_not_connected(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    token, err = ga.fresh_token(secrets, "google_docs", "google_docs:default", {})
    assert token == ""
    assert err and "not connected" in err["error"]


# ------------------------------------------------------------------ scope


def test_identity_scopes_are_added_once_and_only_once():
    scope = "https://www.googleapis.com/auth/calendar"
    once = ga._identity_scope(scope)
    assert once.split() == [scope, "openid", "email"]
    # Re-applying must not duplicate: Google rejects nothing for it, but the consent
    # screen and the stored scope string both become misleading.
    assert ga._identity_scope(once) == once


# ------------------------------------------------------------------ the hook is wired


def test_a_tool_call_refreshes_an_aged_token_and_uses_the_NEW_one(tmp_path, monkeypatch):
    """The refresh only helps if the resolvers actually call it.

    `_google_refreshed` is a single hook inside `_account_profile` / `_gcal_profile` /
    `_profile`, and deleting it breaks nothing that any other test looks at: tools keep
    working right after sign-in and fail for the rest of the day. So assert the whole
    path — an aged profile, a real tool call, and the NEW token on the wire.
    """
    from harness.integrations.connectors import accounts as _accounts
    from harness.integrations.connectors.integration_tools import make_integration_tools

    secrets = SecretStore(tmp_path / "s.json")
    _accounts.add_account(
        secrets,
        "google_drive",
        "me@example.com",
        _profile(expires_at=int(time.time()) - 60, access_token="stale"),
    )

    seen = {}

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"files": []})

    _mock(monkeypatch, handler)
    tools = {t.__name__: t for t in make_integration_tools(secrets)}
    result = tools["drive_recent_files"]()

    assert not result.get("error"), result
    assert seen["auth"] == "Bearer fresh", (
        "the tool sent the stale token — the refresh hook is not wired into this resolver"
    )


def test_a_gmail_tool_call_refreshes_an_aged_token_and_uses_the_NEW_one(
    tmp_path, monkeypatch
):
    """Gmail resolves its mailbox through its OWN function, `_gmail_profile`.

    The Drive test above cannot cover it: three separate resolvers each need the hook,
    and missing one breaks only that connector — silently, an hour after sign-in.
    """
    from harness.integrations.connectors import gmail_accounts
    from harness.integrations.connectors.integration_tools import make_integration_tools

    secrets = SecretStore(tmp_path / "s.json")
    gmail_accounts.managed_connect_account(
        secrets,
        {
            **_profile(expires_at=int(time.time()) - 60, access_token="stale"),
            "account": "me@example.com",
        },
    )

    seen = {}

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"labels": []})

    _mock(monkeypatch, handler)
    tools = {t.__name__: t for t in make_integration_tools(secrets)}
    result = tools["gmail_list_labels"]()

    assert not result.get("error"), result
    assert seen["auth"] == "Bearer fresh", (
        "gmail sent the stale token — the refresh hook is not wired into _gmail_profile"
    )
