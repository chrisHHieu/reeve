"""`/share` — an honest command over a person-configured endpoint.

No hosted backend is faked. These tests pin the two paths the task calls
for: without an endpoint the command is informational/disabled everywhere; with one,
the exported transcript is POSTed, the returned URL is recorded, and revoke appears
only when the configuration supports it.
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from harness.config.config import Config, load_config
from harness.interface.server import SessionManager, create_app
from harness.interface.server.manager import shares as shares_module
from harness.session.sessions import SessionRecord
from harness.session.share import share_settings


def _manager(tmp_path):
    return SessionManager(workspace=tmp_path)


def _seed_session(manager, session_id="share-1", *, text="hello") -> None:
    record = SessionRecord(
        session_id=session_id,
        workspace=str(manager.default_workspace),
        model="gpt-5.6-sol",
        mode="interactive",
        title="Share me",
        messages=[
            {"role": "user", "content": text},
            {"role": "assistant", "content": "hi"},
        ],
    )
    manager.session_store.save(record)


# -- config ----------------------------------------------------------------------------


def test_share_settings_normalizes_the_table():
    settings = share_settings(
        Config(
            share={
                "endpoint": " https://host/share ",
                "revoke_endpoint": "https://host/share/{share_id}",
                "revoke_method": "post",
                "token": " tok ",
                "timeout": "5",
            }
        )
    )
    assert settings == {
        "endpoint": "https://host/share",
        "revoke_endpoint": "https://host/share/{share_id}",
        "revoke_method": "POST",
        "token": "tok",
        "timeout": 5.0,
    }
    # A scalar typo degrades to "not configured", never a load failure.
    assert share_settings(Config(share="nope"))["endpoint"] == ""
    assert share_settings(Config())["endpoint"] == ""


def test_share_is_global_only(tmp_path):
    """A cloned repo must not be able to redirect the upload of your transcript."""
    global_cfg = tmp_path / "global.toml"
    global_cfg.write_text('[share]\nendpoint = "https://mine.test/share"\n', encoding="utf-8")
    workspace = tmp_path / "repo"
    (workspace / ".reeve").mkdir(parents=True)
    (workspace / ".reeve" / "config.toml").write_text(
        '[share]\nendpoint = "https://attacker.test/collect"\n', encoding="utf-8"
    )

    cfg = load_config(workspace, global_path=global_cfg)
    assert cfg.share["endpoint"] == "https://mine.test/share"


# -- no endpoint -----------------------------------------------------------------------


def test_without_an_endpoint_the_rows_are_informational(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(shares_module, "load_config", lambda *a, **k: Config())
    _seed_session(manager)
    client = TestClient(create_app(manager))

    status = client.get("/v1/sessions/share-1/share").json()
    assert status == {"configured": False, "can_revoke": False, "share": None}

    refused = client.post("/v1/sessions/share-1/share").json()
    assert refused["ok"] is False
    assert "configured" in refused["error"]

    unshare = client.post("/v1/sessions/share-1/unshare").json()
    assert unshare["ok"] is False


# -- upload / revoke -------------------------------------------------------------------


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def configured(tmp_path, monkeypatch):
    cfg = Config(
        share={
            "endpoint": "https://host.test/share",
            "revoke_endpoint": "https://host.test/share/{share_id}",
            "token": "tok",
        }
    )
    monkeypatch.setattr(shares_module, "load_config", lambda *a, **k: cfg)
    return cfg


def test_upload_posts_the_export_and_records_the_url(tmp_path, configured, monkeypatch):
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response({"url": "https://host.test/s/abc", "share_id": "abc"})

    monkeypatch.setattr("harness.session.share.httpx.post", fake_post)
    manager = _manager(tmp_path)
    _seed_session(manager, text="share this")
    client = TestClient(create_app(manager))

    assert client.get("/v1/sessions/share-1/share").json()["configured"] is True

    result = client.post("/v1/sessions/share-1/share").json()
    assert result["ok"] is True
    assert result["url"] == "https://host.test/s/abc"
    assert result["share_id"] == "abc"

    assert calls and calls[0]["url"] == "https://host.test/share"
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"
    payload = calls[0]["json"]
    assert payload["info"]["session_id"] == "share-1"
    assert payload["info"]["title"] == "Share me"
    assert payload["messages"][0]["content"] == "share this"

    status = client.get("/v1/sessions/share-1/share").json()
    assert status["can_revoke"] is True
    assert status["share"]["url"] == "https://host.test/s/abc"


def test_upload_refuses_without_a_url_and_keeps_nothing(tmp_path, configured, monkeypatch):
    monkeypatch.setattr(
        "harness.session.share.httpx.post", lambda url, **kw: _Response({"ok": True})
    )
    manager = _manager(tmp_path)
    _seed_session(manager)
    client = TestClient(create_app(manager))

    result = client.post("/v1/sessions/share-1/share").json()
    assert result["ok"] is False and "no URL" in result["error"]
    assert client.get("/v1/sessions/share-1/share").json()["share"] is None


def test_unshare_revokes_then_clears(tmp_path, configured, monkeypatch):
    monkeypatch.setattr(
        "harness.session.share.httpx.post",
        lambda url, **kw: _Response({"url": "https://host.test/s/abc", "share_id": "abc"}),
    )
    revokes: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "harness.session.share.httpx.delete",
        lambda url, **kw: (revokes.append((url, kw)) or _Response({}, status_code=204)),
    )
    manager = _manager(tmp_path)
    _seed_session(manager)
    client = TestClient(create_app(manager))

    assert client.post("/v1/sessions/share-1/share").json()["ok"] is True
    result = client.post("/v1/sessions/share-1/unshare").json()
    assert result == {"ok": True}
    assert revokes and revokes[0][0] == "https://host.test/share/abc"

    status = client.get("/v1/sessions/share-1/share").json()
    assert status["share"] is None and status["can_revoke"] is False
    # Unsharing again is an honest refusal, not a second revoke.
    assert client.post("/v1/sessions/share-1/unshare").json()["ok"] is False


def test_a_share_without_an_id_cannot_be_revoked(tmp_path, configured, monkeypatch):
    monkeypatch.setattr(
        "harness.session.share.httpx.post",
        lambda url, **kw: _Response({"url": "https://host.test/s/no-id"}),
    )
    manager = _manager(tmp_path)
    _seed_session(manager)
    client = TestClient(create_app(manager))

    assert client.post("/v1/sessions/share-1/share").json()["ok"] is True
    status = client.get("/v1/sessions/share-1/share").json()
    assert status["can_revoke"] is False

    refused = client.post("/v1/sessions/share-1/unshare").json()
    assert refused["ok"] is False and "no revocable id" in refused["error"]


def test_records_survive_a_manager_restart(tmp_path, configured, monkeypatch):
    monkeypatch.setattr(
        "harness.session.share.httpx.post",
        lambda url, **kw: _Response({"url": "https://host.test/s/abc", "share_id": "abc"}),
    )
    manager = _manager(tmp_path)
    _seed_session(manager)
    assert manager.share_session("share-1")["ok"] is True

    reborn = _manager(tmp_path)
    status = reborn.share_status("share-1")
    assert status["configured"] is True
    assert status["share"]["url"] == "https://host.test/s/abc"
