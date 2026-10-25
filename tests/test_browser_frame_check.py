"""The Browser tab asks whether a page outside this computer can be shown in a frame, from its
headers, through the web guard, before it tries (and leaves a blank pane)."""

from __future__ import annotations

import httpx

from harness.integrations.web import frameable


def _headers(**values: str) -> httpx.Headers:
    return httpx.Headers({key.replace("_", "-"): value for key, value in values.items()})


def test_a_page_that_refuses_frames_says_why():
    assert frameable.frame_refusal(_headers(x_frame_options="DENY")) == "x-frame-options"
    assert frameable.frame_refusal(_headers(x_frame_options="sameorigin")) == "x-frame-options"
    assert (
        frameable.frame_refusal(_headers(content_security_policy="default-src 'self'; frame-ancestors 'self'"))
        == "frame-ancestors"
    )
    assert frameable.frame_refusal(_headers(content_security_policy="frame-ancestors 'none'")) == "frame-ancestors"


def test_a_page_that_allows_frames_is_allowed():
    assert frameable.frame_refusal(_headers()) is None
    assert frameable.frame_refusal(_headers(content_security_policy="frame-ancestors *")) is None
    assert frameable.frame_refusal(_headers(content_security_policy="default-src 'self'")) is None


class _Response:
    def __init__(self, headers):
        self.headers = headers
        self.closed = False

    def close(self):
        self.closed = True


def test_the_check_goes_through_the_guard_and_never_reads_the_body(monkeypatch):
    seen = {}

    def get_checked(client, url, *, stream=False, **_kw):
        seen["url"], seen["stream"] = url, stream
        seen["response"] = _Response(_headers(x_frame_options="DENY"))
        return seen["response"]

    monkeypatch.setattr("harness.integrations.web.guard.get_checked", get_checked)
    out = frameable.frame_check("https://example.com/page")
    assert out == {"ok": True, "frameable": False, "reason": "x-frame-options"}
    assert seen["url"] == "https://example.com/page" and seen["stream"] is True
    assert seen["response"].closed is True


def test_a_refused_address_or_a_non_web_one_is_not_a_verdict(monkeypatch):
    def refuse(*_a, **_kw):
        raise PermissionError("loopback and private addresses are not fetched")

    monkeypatch.setattr("harness.integrations.web.guard.get_checked", refuse)
    assert frameable.frame_check("http://127.0.0.1:5173/")["ok"] is False
    assert frameable.frame_check("file:///etc/passwd") == {"ok": False, "error": "not a web address"}
    assert frameable.frame_check("javascript:alert(1)")["ok"] is False


def test_the_route_is_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app
    from harness.interface.server.manager import SessionManager
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

    class Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(
        "harness.integrations.web.guard.get_checked",
        lambda *_a, **_kw: _Response(_headers()),
    )
    client = TestClient(create_app(SessionManager(workspace=tmp_path / "ws", provider=Provider())))
    out = client.get("/v1/browser/frame-check", params={"url": "https://example.com/"}).json()
    assert out == {"ok": True, "frameable": True}
