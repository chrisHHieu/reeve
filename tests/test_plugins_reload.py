"""`/reload-plugins` (design §10.1): an open conversation picks up the plugins as
they are now, between turns, without a second engine running beside the first."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app

RELEASE = Path(__file__).parent / "fixtures" / "plugins" / "release-helper"


def _plugin_rows(manager: SessionManager, session_id: str) -> list[dict]:
    return [r for r in manager.session_log(session_id).read() if r.get("type") == "plugins/active"]


def test_a_reload_rebuilds_the_conversation_with_the_plugins_now_on(tmp_path, monkeypatch):
    monkeypatch.delenv("REEVE_PLUGIN_DIRS", raising=False)
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/rp") as ws:
        assert ws.receive_json()["type"] == "ready"
        before = manager._engines["rp"]
        assert _plugin_rows(manager, "rp") == []

        # A plugin appears after the conversation opened; the reload brings it in.
        monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
        reloaded = client.post("/v1/sessions/rp/reload-plugins").json()
        assert reloaded == {"ok": True, "plugins": 1, "errors": 0, "views": 1}
        frame = ws.receive_json()
        assert frame["type"] == "plugins_reloaded" and frame["data"]["plugins"] == 1

        # The socket still holding the old engine may not run a turn on it.
        ws.send_json({"type": "user_message", "text": "hello"})
        rejected = ws.receive_json()
        assert rejected["type"] == "input_rejected"
        assert "reloaded" in rejected["data"]["error"]

    with client.websocket_connect("/ws/session/rp") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert manager._engines["rp"] is not before
    rows = _plugin_rows(manager, "rp")
    assert len(rows) == 1 and rows[0]["reason"] == "reload"
    assert [p["id"] for p in rows[0]["plugins"]] == ["release-helper@inline"]


def test_no_reload_while_a_turn_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    manager.get_engine("busy", workspace=str(tmp_path))
    assert manager.try_mark_running("busy")
    client = TestClient(create_app(manager))
    refused = client.post("/v1/sessions/busy/reload-plugins").json()
    assert refused["ok"] is False and "turn is running" in refused["error"]
    assert "busy" in manager._engines


def test_a_reload_nobody_is_watching_says_so_in_its_answer(tmp_path, monkeypatch):
    """A socket still building its engine is not registered yet: the frame reaches no one,
    so the answer's `views: 0` is what lets the caller say it happened (seen live)."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    manager.get_engine("quiet", workspace=str(tmp_path))
    client = TestClient(create_app(manager))
    assert client.post("/v1/sessions/quiet/reload-plugins").json()["views"] == 0
