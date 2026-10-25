"""A Google token that dies before its stated expiry is renewed once, on the 401.

Renewing by the clock cannot see a revoked grant, a changed password or a rotated refresh
token: the stored `expires_at` still says the token is good, and every call fails until the
person connects again. The request layer therefore renews on the 401 itself and sends the
call again once. No live Google here; httpx MockTransport stands in.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs

import httpx

from harness.integrations.connectors import accounts as _accounts
from harness.integrations.connectors import google_auth as ga
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore
from test_google_auth import _mock, _profile


def _signed_in(tmp_path, **over):
    secrets = SecretStore(tmp_path / "s.json")
    _accounts.add_account(
        secrets, "google_drive", "me@example.com",
        _profile(expires_at=int(time.time()) + 3600, access_token="dead", **over),
    )
    return secrets


def _drive(secrets):
    return {t.__name__: t for t in make_integration_tools(secrets)}["drive_recent_files"]


def test_a_401_renews_the_token_and_the_call_succeeds(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path)
    calls = []

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["refresh_token"]
            return httpx.Response(200, json={"access_token": "alive", "expires_in": 3600})
        calls.append(request.headers.get("authorization"))
        if request.headers.get("authorization") == "Bearer dead":
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"files": []})

    _mock(monkeypatch, handler)
    result = _drive(secrets)()

    assert not result.get("error"), result
    assert calls == ["Bearer dead", "Bearer alive"]
    # and it is stored, so the next call does not hit the 401 again
    stored = _accounts.resolve(secrets, "google_drive", "")[2]
    assert stored["access_token"] == "alive"
    assert stored["refresh_token"] == "rt"


def test_a_refused_renewal_reports_the_401_and_does_not_loop(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path)
    sent = []

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(400, json={"error": "invalid_grant"})
        sent.append(request.headers.get("authorization"))
        return httpx.Response(401, json={"error": "invalid_token"})

    _mock(monkeypatch, handler)
    result = _drive(secrets)()

    assert "401" in str(result.get("error")), result
    assert sent == ["Bearer dead"], "a refused renewal must not send the call again"


def test_the_call_is_sent_again_only_once(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path)
    sent = []

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "also-dead", "expires_in": 3600})
        sent.append(request.headers.get("authorization"))
        return httpx.Response(401, json={})

    _mock(monkeypatch, handler)
    result = _drive(secrets)()

    assert "401" in str(result.get("error")), result
    assert len(sent) == 2


def test_a_hand_pasted_token_is_not_renewed(tmp_path, monkeypatch):
    """Profiles from the old paste flow carry no client id, so there is nothing to renew with."""
    secrets = SecretStore(tmp_path / "s.json")
    _accounts.add_account(secrets, "google_drive", "me@example.com", {"access_token": "pasted"})
    tokens_asked = []

    def handler(request):
        if "oauth2.googleapis.com/token" in str(request.url):
            tokens_asked.append(1)
            return httpx.Response(200, json={"access_token": "x"})
        return httpx.Response(401, json={})

    _mock(monkeypatch, handler)
    result = _drive(secrets)()

    assert "401" in str(result.get("error")), result
    assert tokens_asked == []


def test_a_401_from_a_token_this_process_never_issued_is_left_alone():
    assert ga.renew_after_401("never-seen") is None


def test_the_ledger_is_bounded(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    for number in range(ga._ISSUED_LIMIT + 20):
        ga.note_issued(secrets, "k", {"access_token": f"t{number}", "google_client_id": "cid"})
    assert len(ga._ISSUED) <= ga._ISSUED_LIMIT
