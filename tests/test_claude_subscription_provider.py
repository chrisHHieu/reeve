import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from harness.llm.providers.claude_subscription import (
    AnthropicSubscriptionProvider,
)
from harness.llm.providers.claude_subscription.constants import (
    CALLBACK_PORT,
    CLIENT_ID_ENV,
    REDIRECT_URI,
    TOKEN_URL,
    AnthropicSubscriptionAuthError,
    AnthropicSubscriptionSignInRequired,
)
from harness.llm.providers.claude_subscription.signin import (
    answer_redirect,
    build_authorize_url,
    create_pkce,
)
from harness.llm.providers.claude_subscription.tokens import (
    AnthropicSubscriptionTokenStore,
    exchange_code,
    jwt_claims,
)
from harness.llm.providers.claude_subscription.verify import verify
from harness.llm.providers.registry import (
    build_provider_client,
    descriptor_configured,
    get_descriptor,
)
from harness.llm.providers.matrix import models_for_provider


CLIENT = "00000000-aaaa-bbbb-cccc-000000000000"


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    """No client id ships in the source; these tests run as an operator who set one."""
    monkeypatch.setenv(CLIENT_ID_ENV, CLIENT)


def test_without_a_client_id_the_sign_in_says_what_to_set(monkeypatch):
    monkeypatch.delenv(CLIENT_ID_ENV)
    with pytest.raises(AnthropicSubscriptionAuthError, match=CLIENT_ID_ENV):
        build_authorize_url("s", "c")


class MemorySecretStore:
    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)

    def put(self, key, value):
        self._data[key] = dict(value)

    def delete(self, key):
        return self._data.pop(key, None) is not None


def test_pkce_challenge_shape():
    verifier, challenge = create_pkce()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert "=" not in challenge


def test_authorize_url_parameters():
    url = build_authorize_url("test_state", "test_challenge")
    assert "https://claude.ai/oauth/authorize" in url
    assert f"client_id={CLIENT}" in url
    assert "redirect_uri=" in url
    assert "code_challenge=test_challenge" in url
    assert "code_challenge_method=S256" in url
    assert "code=true" in url


def test_answer_redirect_success():
    req = "GET /callback?code=mock_code&state=mock_state HTTP/1.1\r\n".encode()
    body, code, err = answer_redirect(req, "mock_state")
    assert code == "mock_code"
    assert err is None
    assert b"200 OK" in body


def test_answer_redirect_error_or_mismatch():
    req = b"GET /callback?error=access_denied HTTP/1.1\r\n"
    body, code, err = answer_redirect(req, "expected_state")
    assert code is None
    assert isinstance(err, AnthropicSubscriptionAuthError)

    req2 = b"GET /callback?code=abc&state=wrong_state HTTP/1.1\r\n"
    body, code, err = answer_redirect(req2, "expected_state")
    assert code is None
    assert err is None
    assert b"400 Bad Request" in body


def test_exchange_code():
    with patch("harness.llm.providers.claude_subscription.tokens._token_post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "acc_123",
            "refresh_token": "ref_456",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_resp

        res = exchange_code("mock_code#mock_state", "mock_verifier", "fallback_state")
        assert res["access_token"] == "acc_123"

        # Verify sent payload
        called_payload = mock_post.call_args[0][0]
        assert called_payload["code"] == "mock_code"
        assert called_payload["state"] == "mock_state"
        assert called_payload["code_verifier"] == "mock_verifier"
        assert called_payload["grant_type"] == "authorization_code"


def test_token_store_access_and_refresh():
    store_backend = MemorySecretStore()
    token_store = AnthropicSubscriptionTokenStore(store_backend)
    assert not token_store.signed_in()

    # Signed out raises typed error
    with pytest.raises(AnthropicSubscriptionSignInRequired):
        token_store.access_token()

    # Save mock tokens
    token_store.save({
        "access_token": "valid_token_123",
        "refresh_token": "refresh_token_456",
        "expires_in": 3600,
    })
    assert token_store.signed_in()
    assert token_store.access_token() == "valid_token_123"

    # Near expiry triggers refresh
    token_store.save({
        "access_token": "expiring_soon",
        "refresh_token": "refresh_token_456",
        "expires_in": 10,  # inside margin
    })

    with patch("harness.llm.providers.claude_subscription.tokens._token_post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new_refreshed_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_resp

        token = token_store.access_token()
        assert token == "new_refreshed_token"
        assert token_store.access_token() == "new_refreshed_token"

    # Revocation / 400 clearing
    with patch("harness.llm.providers.claude_subscription.tokens._token_post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_post.return_value = mock_resp

        # Force refresh
        with pytest.raises(AnthropicSubscriptionSignInRequired):
            token_store.refresh()

        assert not token_store.signed_in()


def test_registry_and_descriptors():
    desc = get_descriptor("anthropic-subscription")
    assert desc is not None
    assert desc.auth == "oauth"
    assert not desc.needs_key

    # Configured check
    assert not descriptor_configured(desc, {})
    assert descriptor_configured(desc, {"tokens": {"access_token": "foo"}})

    # Model catalog
    models = models_for_provider("anthropic-subscription")
    assert len(models) >= 4
    assert any("claude-sonnet" in m for m in models)


def test_verify_flow():
    store_backend = MemorySecretStore()
    assert not verify(store_backend)["ok"]

    token_store = AnthropicSubscriptionTokenStore(store_backend)
    token_store.save({
        "access_token": "mock_token",
        "refresh_token": "mock_refresh",
        "expires_in": 3600,
    })

    with patch("httpx.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        mock_post.return_value = resp

        res = verify(store_backend)
        assert res["ok"] is True
        headers = mock_post.call_args[1]["headers"]
        assert "Bearer mock_token" in headers["authorization"]
        assert "oauth-2025-04-20" in headers["anthropic-beta"]


def test_provider_headers_and_client():
    store_backend = MemorySecretStore()
    token_store = AnthropicSubscriptionTokenStore(store_backend)
    token_store.save({
        "access_token": "test_access_token_abc",
        "expires_in": 3600,
    })

    provider = AnthropicSubscriptionProvider(secrets=store_backend)
    with patch("anthropic.Anthropic") as mock_anthropic:
        provider._ensure_client()
        mock_anthropic.assert_called_once()
        kwargs = mock_anthropic.call_args[1]
        assert kwargs["auth_token"] == "test_access_token_abc"
        assert kwargs["default_headers"]["authorization"] == "Bearer test_access_token_abc"
        assert "oauth-2025-04-20" in kwargs["default_headers"]["anthropic-beta"]
