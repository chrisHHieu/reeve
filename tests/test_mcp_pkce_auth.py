"""Authorization-code + PKCE against a pre-registered public client.

Google Workspace forced this third sign-in shape (2026-09-06): `accounts.google.com`
publishes no `registration_endpoint`, so DCR cannot run, but it accepts installed clients,
so the browser redirect works and the user copies nothing.

This module is the BROWSER half only — it returns the vendor's raw token response and
stores nothing. Storage and refresh belong to whoever owns the profile, and are covered by
tests/test_google_auth.py.

No live vendor here — httpx MockTransport stands in. Two of these guard failures that are
SILENT in production:

  - a missing `access_type=offline` yields no refresh token, and the connector dies one
    hour after a sign-in that looked perfect;
  - a scope widened to Google's RESTRICTED tier still works, and only reveals itself as a
    demand for a paid annual security assessment at launch.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from harness.extensions.mcp import pkce_auth as pk


def _client(handler):
    """Patch httpx.AsyncClient so the module's own calls hit the mock."""
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    return factory


def _profile(secrets, name="google_calendar"):
    return secrets.get(pk.PROFILE_PREFIX + name) or {}


# --------------------------------------------------------------- the authorize URL


def test_the_challenge_is_a_real_s256_of_the_verifier():
    verifier, challenge = pk._pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge  # padding would break the vendor's comparison


def test_vendor_extras_reach_the_authorize_url():
    """Google returns NO refresh token without access_type=offline + prompt=consent.

    The failure is invisible: sign-in succeeds, tools work, and the connector dies an
    hour later. Nothing in the flow reports it, so the guard belongs here.
    """
    url = pk.build_authorize_url(
        auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        client_id="cid.apps.googleusercontent.com",
        scope="https://www.googleapis.com/auth/calendar",
        state="st",
        challenge="ch",
        extra={"access_type": "offline", "prompt": "consent"},
    )
    q = parse_qs(urlsplit(url).query)
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == [pk.redirect_uri()]


# --------------------------------------------------------------- the exchange


def test_a_public_client_sends_no_empty_secret(tmp_path, monkeypatch):
    """Omitting the field and sending it empty are not the same; some servers refuse."""
    seen = {}

    def handler(request):
        # keep_blank_values, or `client_secret=` and no field at all look identical and
        # the assertion below cannot fail.
        seen["body"] = parse_qs(request.content.decode(), keep_blank_values=True)
        return httpx.Response(200, json={"access_token": "at", "expires_in": 60})

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    asyncio.run(
        pk._exchange(
            token_endpoint="https://x/token",
            client_id="cid",
            client_secret="",
            code="c",
            verifier="v",
        )
    )
    assert "client_secret" not in seen["body"]


def test_a_refused_exchange_raises_rather_than_storing_nothing(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(
            400, json={"error": "invalid_client", "error_description": "no such client"}
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    with pytest.raises(pk.PkceAuthError) as exc:
        asyncio.run(
            pk._exchange(
                token_endpoint="https://x/token",
                client_id="cid",
                client_secret="",
                code="c",
                verifier="v",
            )
        )
    assert "no such client" in str(exc.value)


# --------------------------------------------------------------- the CASA guard


#: Google's RESTRICTED tier. Any one of these on a shipped descriptor drags the whole
#: Cloud project into a paid annual CASA security assessment.
_RESTRICTED = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.metadata",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
}


def test_connectors_sharing_a_cloud_project_share_a_verification_tier():
    """Google's consent screen — and therefore its verification tier — is PER PROJECT.

    One restricted scope drags every connector in that project into a paid annual CASA
    security assessment. So the invariant is not "no restricted scopes" (Gmail needs one:
    all 23 tools Google's own MCP server offers refuse anything narrower than
    `gmail.modify`). The invariant is that a project must not MIX tiers — adding Gmail's
    scope to the Workspace client, or moving Calendar under Gmail's, would silently put
    five free connectors behind a paid assessment.

    Projects are identified here by the env var naming their client, which is the only
    thing in the descriptor that distinguishes them.
    """
    from collections import defaultdict

    from harness.integrations.connectors.descriptors import list_descriptors

    projects = defaultdict(dict)
    for d in list_descriptors():
        if not getattr(d, "pkce_auth", False):
            continue
        restricted = _RESTRICTED & set((d.oauth_scope or "").split())
        projects[d.oauth_client_id_env][d.name] = sorted(restricted)

    assert projects, "no pkce connectors found — this guard has stopped guarding"
    for env_key, members in projects.items():
        tiers = {bool(v) for v in members.values()}
        assert len(tiers) == 1, (
            f"{env_key} mixes verification tiers: {members}. Split the restricted "
            f"connector into its own Cloud project, or the whole project needs CASA."
        )

    # And the split we deliberately made must still be in place.
    assert projects["GOOGLE_OAUTH_CLIENT_ID"] == {
        "google_calendar": [],
        "google_drive": [],
        "google_docs": [],
        "google_sheets": [],
        "google_slides": [],
    }
    assert list(projects["GMAIL_OAUTH_CLIENT_ID"]) == ["gmail"]
    assert projects["GMAIL_OAUTH_CLIENT_ID"]["gmail"], (
        "gmail without a restricted scope cannot reach the tools it declares"
    )


def test_every_google_connector_asks_for_offline_access():
    """Without it Google issues no refresh token and the connector dies in an hour."""
    from harness.integrations.connectors.descriptors import list_descriptors

    for d in list_descriptors():
        # Microsoft asks for a refresh token by scope, not by parameter (tests/test_microsoft.py).
        if not getattr(d, "pkce_auth", False) or d.pkce_provider != "google":
            continue
        extra = dict(d.pkce_auth_extra or ())
        assert extra.get("access_type") == "offline", d.name
        assert extra.get("prompt") == "consent", d.name
        assert d.pkce_auth_endpoint and d.oauth_token_endpoint, d.name
        assert d.oauth_scope, d.name


def test_one_click_is_hidden_while_no_client_id_is_shipped():
    """An unregistered build must not offer a button that dead-ends on invalid_client."""
    from harness.integrations.connectors.descriptors import get_descriptor
    from harness.integrations.connectors.lifecycle.listing import _mcp_one_click_ready

    d = get_descriptor("google_calendar")
    assert d.pkce_auth is True
    # Shipped id is empty until the Cloud project exists.
    assert _mcp_one_click_ready(d) is bool((d.oauth_client_id or "").strip())


def test_an_mcp_only_connector_is_not_offered_before_its_client_id_exists(monkeypatch):
    """Docs/Sheets/Slides have NO manual field, so with no client id their card would
    render an empty form with nothing to press — a dead card, not a degraded one.

    Calendar and Drive keep a token field, so they stay available either way.
    """
    import importlib

    import harness.integrations.connectors.descriptor.oauth_clients as desc_types
    import harness.integrations.connectors.descriptor_data as desc_data
    import harness.integrations.connectors.descriptors as desc

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    importlib.reload(desc_types); importlib.reload(desc_data); importlib.reload(desc)
    try:
        for name in ("google_docs", "google_sheets", "google_slides"):
            d = desc.get_descriptor(name)
            assert d.fields == [], f"{name} gained a manual field; revisit this gate"
            assert d.available is False, name
        for name in ("google_calendar", "google_drive"):
            assert desc.get_descriptor(name).available is True, name

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "x.apps.googleusercontent.com")
        importlib.reload(desc_types); importlib.reload(desc_data); importlib.reload(desc)
        for name in ("google_docs", "google_sheets", "google_slides"):
            assert desc.get_descriptor(name).available is True, name
    finally:
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        importlib.reload(desc_types); importlib.reload(desc_data); importlib.reload(desc)
