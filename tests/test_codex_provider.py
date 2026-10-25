"""The ChatGPT-subscription provider (`openai-codex`): signing in through the browser, keeping and
refreshing its tokens, what a request to the subscription backend carries, how its failures read,
and the routes the settings page drives.

The token endpoint, the SDK client and the check's probe are replaced; the loopback page the
browser returns to is served for real, on its fixed port.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers.codex import CodexProvider, constants, signin, tokens
from harness.llm.providers.codex.constants import CODEX_BASE_URL, CodexAuthError, CodexSignInRequired
from harness.llm.providers.codex.signin import build_authorize_url, create_pkce
from harness.llm.providers.codex.tokens import CodexTokenStore, account_id_from
from harness.llm.providers.codex.verify import verify
from harness.safety.secrets import SecretStore

CLAIM = "https://api.openai.com/auth"
PROFILE = "provider:openai-codex"
HI = [{"role": "user", "content": "hi"}]


def _unsigned_jwt(claims: dict) -> str:
    def part(value) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def _access_token(expires_in: float = 3600, account: str = "acct_1") -> str:
    return _unsigned_jwt({"exp": time.time() + expires_in, CLAIM: {"chatgpt_account_id": account}})


def _id_token(email: str = "user@example.com", account: str = "acct_1") -> str:
    return _unsigned_jwt({"email": email, CLAIM: {"chatgpt_account_id": account}})


CLIENT = "app_test0client"


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    """No client id ships in the source; these tests run as an operator who set one."""
    monkeypatch.setenv(constants.CLIENT_ID_ENV, CLIENT)


def test_without_a_client_id_the_sign_in_says_what_to_set(monkeypatch):
    monkeypatch.delenv(constants.CLIENT_ID_ENV)
    with pytest.raises(CodexAuthError, match=constants.CLIENT_ID_ENV):
        build_authorize_url("s", "c")


def _token_answer(status: int = 200, body: dict | None = None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


def _signed_in(tmp_path, expires_in: float = 3600) -> SecretStore:
    secrets = SecretStore(tmp_path / "s.json")
    CodexTokenStore(secrets).save({"access_token": _access_token(expires_in), "refresh_token": "rt-1", "id_token": _id_token()})
    return secrets


# -- the authorize link --------------------------------------------------------------------------


def test_the_pkce_challenge_is_the_s256_of_a_fresh_verifier():
    verifier, challenge = create_pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert create_pkce()[0] != verifier


def test_the_authorize_link_asks_for_a_code_back_on_the_loopback_page():
    link = urlsplit(build_authorize_url("st4te", "ch4llenge"))
    assert f"{link.scheme}://{link.netloc}{link.path}" == constants.AUTHORIZE_URL
    query = {key: values[0] for key, values in parse_qs(link.query).items()}
    assert query == {**query,
        "response_type": "code", "client_id": CLIENT,
        "redirect_uri": "http://localhost:1455/auth/callback", "state": "st4te",
        "code_challenge": "ch4llenge", "code_challenge_method": "S256",
        "codex_cli_simplified_flow": "true", "originator": constants.ORIGINATOR,
    }


def test_the_account_id_is_read_from_the_id_token_then_the_access_token():
    assert account_id_from({"id_token": _id_token(account="acct_9")}) == "acct_9"
    assert account_id_from({"access_token": _unsigned_jwt({CLAIM: {"account_id": "acct_x"}})}) == "acct_x"
    assert account_id_from({"access_token": "not-a-jwt"}) == ""


# -- signing in ----------------------------------------------------------------------------------


async def _browser_returns(query: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", constants.CALLBACK_PORT)
    writer.write(f"GET /auth/callback?{query} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    page = await reader.read(-1)
    writer.close()
    return page.decode()


async def _started(secrets, monkeypatch) -> tuple[asyncio.Task, dict]:
    opened: dict = {}
    monkeypatch.setattr("webbrowser.open", lambda url: opened.update(url=url))
    flow = asyncio.create_task(signin.sign_in(secrets))
    while not opened:  # the flow has bound its port and opened the browser
        await asyncio.sleep(0.01)
    return flow, opened


async def test_a_sign_in_exchanges_the_returned_code_and_keeps_the_tokens(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    exchanged: dict = {}

    def token_endpoint(data, timeout=30.0):
        exchanged.update(data)
        return _token_answer(body={"access_token": _access_token(), "refresh_token": "rt-1", "id_token": _id_token("user@example.com")})

    monkeypatch.setattr(tokens, "_token_post", token_endpoint)
    flow, opened = await _started(secrets, monkeypatch)
    state = parse_qs(urlsplit(opened["url"]).query)["state"][0]

    # A local request with the wrong state is refused and leaves the flow waiting.
    assert (await _browser_returns("code=evil&state=wrong")).startswith("HTTP/1.1 400")
    assert not flow.done()
    page = await _browser_returns(f"code=c0de&state={state}")
    assert page.startswith("HTTP/1.1 200") and "close this tab" in page.lower()
    assert await flow == {"ok": True, "account": "user@example.com"}

    assert (exchanged["grant_type"], exchanged["code"], exchanged["redirect_uri"]) == ("authorization_code", "c0de", "http://localhost:1455/auth/callback")
    assert exchanged["code_verifier"]
    stored = secrets.get(PROFILE)
    assert (stored["tokens"]["refresh_token"], stored["account_id"], stored["account_email"]) == ("rt-1", "acct_1", "user@example.com")
    assert isinstance(stored["tokens_issued_at"], int)


async def test_an_error_the_browser_brings_back_ends_the_sign_in_and_stores_nothing(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    flow, _ = await _started(secrets, monkeypatch)
    assert (await _browser_returns("error=access_denied")).startswith("HTTP/1.1 400")
    with pytest.raises(CodexAuthError, match="access_denied"):
        await flow
    assert not CodexTokenStore(secrets).signed_in()


async def test_a_taken_loopback_port_is_named(tmp_path):
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        holder.bind(("127.0.0.1", constants.CALLBACK_PORT))
        holder.listen(1)
        with pytest.raises(CodexAuthError, match="1455"):
            await signin.sign_in(SecretStore(tmp_path / "s.json"), open_browser=False)
    finally:
        holder.close()


# -- keeping the tokens fresh ----------------------------------------------------------------------


def test_a_fresh_access_token_is_used_as_it_is(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path)
    monkeypatch.setattr(tokens, "_token_post", lambda *a, **k: pytest.fail("no refresh was needed"))
    assert CodexTokenStore(secrets).access_token() == (secrets.get(PROFILE)["tokens"]["access_token"], "acct_1")


def test_a_token_close_to_expiry_is_refreshed_and_what_the_refresh_omits_is_kept(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path, expires_in=30)  # inside the refresh margin
    asked: dict = {}
    fresh = _access_token(7200)
    monkeypatch.setattr(tokens, "_token_post", lambda data, timeout=30.0: asked.update(data) or _token_answer(body={"access_token": fresh}))
    assert CodexTokenStore(secrets).access_token() == (fresh, "acct_1")
    assert asked == {"grant_type": "refresh_token", "refresh_token": "rt-1", "client_id": CLIENT}
    kept = secrets.get(PROFILE)["tokens"]
    assert kept["access_token"] == fresh and kept["refresh_token"] == "rt-1"


def test_a_refused_refresh_signs_out_cleanly(tmp_path, monkeypatch):
    secrets = _signed_in(tmp_path, expires_in=-10)
    monkeypatch.setattr(tokens, "_token_post", lambda *a, **k: _token_answer(status=400))
    store = CodexTokenStore(secrets)
    with pytest.raises(CodexSignInRequired):
        store.access_token()
    assert not store.signed_in()  # signed out, not a loop of failing refreshes


@pytest.mark.parametrize("call", ["token", "provider"])
def test_signed_out_is_its_own_error(tmp_path, call):
    secrets = SecretStore(tmp_path / "s.json")
    with pytest.raises(CodexSignInRequired, match="Not signed in"):
        if call == "token":
            CodexTokenStore(secrets).access_token()
        else:
            CodexProvider(secrets=secrets).complete(model="gpt-5.2-codex", messages=HI)


# -- requests to the subscription backend ----------------------------------------------------------


class _Backend:
    """The SDK client: raises the queued errors, then streams `events`."""

    def __init__(self, events=(), errors=()):
        self.request: dict = {}
        pending = list(errors)

        def create(**request):
            self.request = request
            if pending:
                raise pending.pop(0)
            return iter(events)

        self.responses = SimpleNamespace(create=create)


def _finished(text="hello"):
    output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(output=output, status="completed", incomplete_details=None))


def _http_error(status: int, message: str):
    error = Exception(message)
    error.status_code = status
    return error


def _codex(tmp_path, monkeypatch, *backends):
    """A provider over signed-in tokens; each SDK client it builds is the next of `backends`, and
    what it was built with is kept."""
    secrets = _signed_in(tmp_path)
    built: list[dict] = []
    queue = list(backends)
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: built.append(kwargs) or queue.pop(0))
    return CodexProvider(secrets=secrets), secrets, built


def test_a_request_carries_the_account_and_the_responses_shape_the_backend_takes(tmp_path, monkeypatch):
    backend = _Backend(events=[_finished()])
    provider, secrets, built = _codex(tmp_path, monkeypatch, backend)
    chunks = list(provider.stream(
        model="gpt-5.2-codex",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
    ))
    client = built[0]
    assert client["api_key"] == secrets.get(PROFILE)["tokens"]["access_token"] and client["base_url"] == CODEX_BASE_URL
    headers = client["default_headers"]
    assert (headers["chatgpt-account-id"], headers["originator"], headers["OpenAI-Beta"]) == ("acct_1", constants.ORIGINATOR, "responses=experimental")
    assert headers["session-id"]  # one per conversation
    sent = backend.request
    assert (sent["model"], sent["stream"], sent["store"], sent["instructions"]) == ("gpt-5.2-codex", True, False, "sys")
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["tools"] == [{"type": "function", "name": "f", "strict": False}]
    assert chunks[-1].turn.text == "hello"


def test_a_plain_completion_is_streamed_and_carries_the_reasoning_effort(tmp_path, monkeypatch):
    backend = _Backend(events=[_finished("done")])
    provider, _, _ = _codex(tmp_path, monkeypatch, backend)
    assert provider.complete(model="gpt-5.2-codex", messages=HI, reasoning_effort="high").text == "done"
    assert backend.request["stream"] is True  # the backend serves streams only
    assert backend.request["reasoning"] == {"summary": "auto", "effort": "high"}
    assert backend.request["instructions"]  # a bare call still sends instructions


def test_a_401_refreshes_the_token_once_and_asks_again_with_the_new_one(tmp_path, monkeypatch):
    provider, _, built = _codex(tmp_path, monkeypatch, _Backend(errors=[_http_error(401, "Unauthorized")]), _Backend(events=[_finished("after refresh")]))
    fresh = _access_token(7200)
    monkeypatch.setattr(tokens, "_token_post", lambda *a, **k: _token_answer(body={"access_token": fresh}))
    assert provider.complete(model="gpt-5.2-codex", messages=HI).text == "after refresh"
    assert len(built) == 2 and built[1]["api_key"] == fresh


def test_a_429_is_said_as_the_plans_limit(tmp_path, monkeypatch):
    provider, _, _ = _codex(tmp_path, monkeypatch, _Backend(errors=[_http_error(429, "Too Many Requests")]))
    with pytest.raises(RuntimeError, match="plan limit"):
        provider.complete(model="gpt-5.2-codex", messages=HI)


# -- the provider in the catalog -------------------------------------------------------------------


def test_the_registry_builds_it_as_a_sign_in_provider_with_no_fields():
    from harness.llm.providers.registry import build_provider_client, descriptor_configured, get_descriptor

    assert isinstance(build_provider_client("openai-codex", {}, None), CodexProvider)
    descriptor = get_descriptor("openai-codex")
    assert descriptor.auth == "oauth" and descriptor.fields == [] and descriptor.to_dict()["auth"] == "oauth"
    # Set up means it holds tokens.
    assert not descriptor_configured(descriptor, {})
    assert descriptor_configured(descriptor, {"tokens": {"access_token": "a"}})


def test_the_subscriptions_models_are_a_curated_list():
    from harness.llm.providers.capabilities import capabilities_for
    from harness.llm.providers.matrix import models_for_provider

    assert models_for_provider("openai-codex") == ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.2-codex", "gpt-5.2", "gpt-5.1-codex", "gpt-5.1-codex-mini"]
    caps = capabilities_for("openai-codex:gpt-5.6-sol")
    assert caps.tools and caps.vision and caps.streaming


# -- checking the sign-in ------------------------------------------------------------------------


def _probe(tmp_path, monkeypatch, status: int):
    secrets = _signed_in(tmp_path)
    seen: dict = {}
    monkeypatch.setattr("httpx.post", lambda url, headers=None, json=None, timeout=None: seen.update(url=url, headers=headers, body=json) or SimpleNamespace(status_code=status))
    return verify(secrets), seen


def test_a_check_while_signed_out_says_so(tmp_path):
    result = verify(SecretStore(tmp_path / "s.json"))
    assert result["ok"] is False and result["state"] == "signed_out"


def test_a_check_probes_the_backend_as_the_account(tmp_path, monkeypatch):
    result, seen = _probe(tmp_path, monkeypatch, 200)
    assert result == {"ok": True, "account": "user@example.com"}
    assert seen["url"] == CODEX_BASE_URL + "/responses"
    assert seen["headers"]["Authorization"].startswith("Bearer ") and seen["headers"]["chatgpt-account-id"] == "acct_1"
    assert seen["body"]["store"] is False and seen["body"]["stream"] is True


def test_an_expired_sign_in_fails_the_check_and_a_used_up_window_does_not(tmp_path, monkeypatch):
    expired, _ = _probe(tmp_path, monkeypatch, 401)
    assert expired["ok"] is False and expired["state"] == "expired"
    limited, _ = _probe(tmp_path, monkeypatch, 429)
    assert limited["ok"] is True and "note" in limited  # signed in; the window is used up


# -- the settings routes ---------------------------------------------------------------------------


def _server(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data")
    return manager, TestClient(create_app(manager))


def _row(client):
    return {row["name"]: row for row in client.get("/v1/providers").json()}["openai-codex"]


def test_the_providers_list_shows_whether_the_subscription_is_signed_in_and_never_its_tokens(tmp_path):
    manager, client = _server(tmp_path)
    row = _row(client)
    assert row["auth"] == "oauth" and row["signed_in"] is False and row["configured"] is False
    assert "gpt-5.6-sol" in row["suggested_models"]
    manager.secrets.put(PROFILE, {"tokens": {"access_token": "a", "refresh_token": "r"}, "account_email": "u@x.com"})
    row = _row(client)
    assert row["signed_in"] is True and row["configured"] is True and row["account"] == "u@x.com"
    assert "tokens" not in row.get("values", {})


def test_the_sign_in_route_starts_the_flow_in_the_background_and_says_so_at_once(tmp_path, monkeypatch):
    manager, _ = _server(tmp_path)
    called = []

    async def flow():
        called.append(True)
        return {"ok": True}

    monkeypatch.setattr(manager, "codex_signin", flow)
    client = TestClient(create_app(manager))
    assert client.post("/v1/providers/openai-codex/signin").json() == {"ok": True, "started": True}
    assert called
    # Flagged before the task ran, so the first status after the click already says authorizing.
    assert manager._codex_authorizing is True


def test_the_status_route_follows_a_sign_in_and_a_sign_out(tmp_path):
    manager, client = _server(tmp_path)

    def status():
        return client.get("/v1/providers/openai-codex/status").json()

    assert status()["signed_in"] is False and status()["authorizing"] is False
    manager.secrets.put(PROFILE, {"tokens": {"access_token": "a"}, "account_email": "u@x.com"})
    assert status()["signed_in"] is True and status()["account"] == "u@x.com"
    assert client.post("/v1/providers/openai-codex/signout").json() == {"ok": True, "had_tokens": True}
    assert status()["signed_in"] is False


def test_the_check_route_reports_signed_out(tmp_path):
    _, client = _server(tmp_path)
    result = client.post("/v1/providers/verify", json={"name": "openai-codex"}).json()
    assert result["ok"] is False and result["state"] == "signed_out"


async def test_a_finished_sign_in_adds_the_subscriptions_model(tmp_path, monkeypatch):
    manager, _ = _server(tmp_path)

    async def sign_in(secrets, **kwargs):
        CodexTokenStore(secrets).save({"access_token": _access_token(), "refresh_token": "rt-1", "id_token": _id_token()})
        return {"ok": True, "account": "user@example.com"}

    monkeypatch.setattr(signin, "sign_in", sign_in)
    assert (await manager.codex_signin())["ok"] is True
    assert manager._codex_authorizing is False
    assert "openai-codex:gpt-5.6-sol" in manager.get_settings()["models"]


async def test_a_failed_sign_in_leaves_its_reason_in_the_status(tmp_path, monkeypatch):
    manager, client = _server(tmp_path)

    async def sign_in(secrets, **kwargs):
        raise CodexAuthError("Port 1455 is already in use — quit the other holder.")

    monkeypatch.setattr(signin, "sign_in", sign_in)
    assert (await manager.codex_signin())["ok"] is False
    status = client.get("/v1/providers/openai-codex/status").json()
    assert "1455" in status["last_error"] and status["authorizing"] is False
