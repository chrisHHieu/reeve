"""The composer's context-window bar: ON by default, OFF only when asked.

Settings lost its toggle on the belief that the default was already ON; it was
OFF, so the bar became unreachable. The default is now ON and the pref survives as an API
knob that persists across a manager restart.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager


def test_the_bar_is_on_by_default_and_off_only_when_asked(tmp_path):
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert client.get("/v1/settings").json()["context_bar"] is True
    assert client.post("/v1/settings/context-bar", json={"context_bar": False}).json()["context_bar"] is False

    restarted = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert restarted.get("/v1/settings").json()["context_bar"] is False
