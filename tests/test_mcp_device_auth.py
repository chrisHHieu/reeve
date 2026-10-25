"""OAuth device grant (RFC 8628) — the flow for vendors that offer no DCR.

GitHub forced it (2026-09-05): no `registration_endpoint`, and a token endpoint that
demands a client secret even with PKCE, which a desktop app cannot keep. The device grant
is the one GitHub offers that needs no secret.

No live vendor here — httpx MockTransport stands in. These guard OUR half: the polling
contract, where tokens land, and what the screen is told.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from harness.extensions.mcp import device_auth as da
from harness.safety.secrets import SecretStore


def _client(handler):
    """Patch httpx.AsyncClient so the module's own calls hit the mock."""
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    return factory


def test_request_code_sends_no_secret_and_returns_what_the_screen_needs(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    code = asyncio.run(
        da.request_code(
            device_endpoint="https://github.com/login/device/code",
            client_id="Iv1.public",
            scope="repo",
        )
    )
    # the whole point of this grant: nothing confidential leaves the machine
    assert "client_id=Iv1.public" in seen["body"]
    assert "secret" not in seen["body"].lower()
    d = code.to_dict()
    assert d["user_code"] == "WDJB-MJHT"
    assert d["open_url"] == "https://github.com/login/device"
    # GitHub sends no verification_uri_complete, so the user must paste
    assert d["needs_code_entry"] is True


def test_a_prefilled_url_removes_the_paste(monkeypatch):
    """Vendors that DO send verification_uri_complete get a true one-click; the flag
    tells the UI which of the two screens to show."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "ABCD-1234",
                "verification_uri": "https://v.example/device",
                "verification_uri_complete": "https://v.example/device?code=ABCD-1234",
                "interval": 1,
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    code = asyncio.run(
        da.request_code(device_endpoint="https://v.example/code", client_id="x")
    )
    d = code.to_dict()
    assert d["open_url"] == "https://v.example/device?code=ABCD-1234"
    assert d["needs_code_entry"] is False


def test_pending_and_slow_down_are_not_failures(monkeypatch):
    """RFC 8628 §3.5: `authorization_pending` means the user is still typing, and
    `slow_down` means that AND back off. Treating either as an error is the classic bug
    — it aborts the flow while the user is mid-approval."""
    replies = [
        {"error": "authorization_pending"},
        {"error": "slow_down"},
        {"error": "authorization_pending"},
        {"access_token": "gho_x", "token_type": "bearer", "scope": "repo"},
    ]
    calls = {"n": 0}

    def handler(request):
        body = replies[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    code = da.DeviceCode({"device_code": "dc", "user_code": "u", "interval": 0})
    raw = asyncio.run(
        da.poll_for_token(
            token_endpoint="https://t.example/token", client_id="x", code=code
        )
    )
    assert raw["access_token"] == "gho_x"
    assert calls["n"] == 4  # it kept waiting through all three non-fatal replies


def test_a_real_refusal_stops_immediately(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json={"error": "access_denied", "error_description": "user said no"}
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    code = da.DeviceCode({"device_code": "dc", "user_code": "u", "interval": 0})
    with pytest.raises(da.DeviceFlowError, match="user said no"):
        asyncio.run(
            da.poll_for_token(
                token_endpoint="https://t.example/token", client_id="x", code=code
            )
        )


def test_tokens_land_where_the_transport_reads_them(tmp_path):
    """Device tokens must share the OAuth profile shape: client.py reads ONE place
    regardless of which grant produced the token, and mcp.json — plain text, routinely
    pasted between machines — never sees a credential."""
    secrets = SecretStore(tmp_path / "s.json")
    assert da.has_tokens("github", secrets) is False
    assert da.bearer_header("github", secrets) is None

    da.store_tokens(
        "github",
        {"access_token": "gho_abc", "token_type": "bearer", "ignored": "x"},
        secrets,
    )
    assert da.has_tokens("github", secrets) is True
    assert da.bearer_header("github", secrets) == {"Authorization": "Bearer gho_abc"}
    stored = secrets.get("mcp-oauth:github")
    assert "ignored" not in stored["tokens"]
    assert stored["tokens_issued_at"] > 0

    assert da.sign_out("github", secrets) is True
    assert da.bearer_header("github", secrets) is None
