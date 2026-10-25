"""`POST /v1/restart`: the server starts itself again so code edited since it started runs.

An agent working on Reeve restarted the server it ran in by starting a second one in the
background and killing the first by pid, eight times in one session (2026-10-15)."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import supervision
from harness.interface.server.app import create_app
from harness.interface.server.app import endpoints
from harness.interface.server.manager import SessionManager
from harness.interface.server.run import successor_command


def _client(tmp_path):
    app = create_app(SessionManager(data_dir=tmp_path / "data"))
    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    return app, server


def test_a_restart_answers_first_then_stops_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(endpoints, "RESTART_AFTER_SECONDS", 0.05)
    monkeypatch.delenv("REEVE_EXIT_WITH_PARENT", raising=False)
    app, server = _client(tmp_path)
    with TestClient(app) as client:
        answer = client.post("/v1/restart").json()
        assert answer["ok"] is True and answer["restarting"] is True
        assert server.should_exit is False  # the caller has its answer before anything stops
        deadline = time.monotonic() + 2
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.02)
    assert server.should_exit is True
    assert app.state.restart_requested is True


def test_a_desktop_apps_server_is_the_apps_to_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_EXIT_WITH_PARENT", "1")
    app, server = _client(tmp_path)
    with TestClient(app) as client:
        answer = client.post("/v1/restart").json()
    assert answer["ok"] is False and "desktop app" in answer["error"]
    assert not getattr(app.state, "restart_requested", False)


def test_the_successor_runs_the_same_command_line(monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "harness.interface.server.run", "--port", "8765"], raising=False)
    assert successor_command() == [sys.executable, "-m", "harness.interface.server.run", "--port", "8765"]


@pytest.mark.parametrize("asked", [True, False])
def test_serve_forever_says_whether_a_restart_was_asked(monkeypatch, asked):
    import uvicorn

    class Server:
        def __init__(self, config):
            self.should_exit = False

        async def serve(self):
            return None

    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: SimpleNamespace(app=app))
    app = SimpleNamespace(state=SimpleNamespace(restart_requested=asked))
    assert supervision.serve_forever(lambda: app, "127.0.0.1", 1) is asked
