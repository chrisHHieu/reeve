"""The browser-backend picker (Connectors ▸ browser): one backend at a time.

The built-in `browser` server's child command follows the `mcp:browser` preference, so
the API validates exactly what the launcher would otherwise silently fall back on
(attach without an endpoint) and releases live browsers when the choice changes.
Stagehand was a third backend until 2026-09-30.
"""

from __future__ import annotations

import threading

from harness.extensions.mcp.config import builtin_mcp_servers as real_builtin_mcp_servers
from harness.interface.server.manager import SessionManager

_BROWSER_ENV = (
    "REEVE_BROWSER_BACKEND",
    "REEVE_BROWSER_MODE",
    "REEVE_BROWSER_ENDPOINT",
    "REEVE_BROWSER_HEADLESS",
)


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    for key in _BROWSER_ENV:
        monkeypatch.delenv(key, raising=False)
    # conftest clears built-ins for arming tests; this file is about the built-in
    # browser's own command, so put the real builder back.
    monkeypatch.setattr(
        "harness.extensions.mcp.config.builtin_mcp_servers", real_builtin_mcp_servers
    )
    return SessionManager(data_dir=tmp_path / "data")


def test_browser_config_exposes_the_current_choice_and_options(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    cfg = mgr.browser_config()
    assert cfg["backend"] == "playwright" and cfg["mode"] == "isolated"
    assert "playwright" in cfg["launch"]
    # One browser now: no backend list to pick from.
    assert "backends" not in cfg and "stagehand" not in cfg


def test_saving_a_backend_persists_it_and_releases_live_browsers(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)

    class _Conn:
        def __init__(self) -> None:
            self.shutdown = threading.Event()
            self.tools = ["navigate"]

    live = _Conn()
    mgr.mcp._conns[("s1", "browser")] = live  # a per-session browser is running

    out = mgr.set_browser_config({"headless": False})
    assert out["ok"] is True
    assert out["backend"] == "playwright" and out["headless"] is False
    assert "--headless" not in out["launch"]
    assert mgr.secrets.get("mcp:browser")["headless"] is False
    # The child command changed: the old browser must not be reused.
    assert live.shutdown.is_set()
    assert not mgr.mcp.connected("browser")


def test_attach_mode_requires_an_endpoint(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    out = mgr.set_browser_config({"backend": "playwright", "mode": "attach"})
    assert out["ok"] is False and "endpoint" in out["error"]
    ok = mgr.set_browser_config(
        {
            "backend": "playwright",
            "mode": "attach",
            "endpoint": "http://127.0.0.1:9222",
        }
    )
    assert ok["ok"] is True and ok["mode"] == "attach"
    assert "--cdp-endpoint" in ok["launch"] and "--isolated" not in ok["launch"]


def test_the_removed_backends_are_refused_and_read_as_playwright(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    for gone in ("stagehand", "chrome-devtools"):
        refused = mgr.set_browser_config({"backend": gone})
        assert refused["ok"] is False and "unknown backend" in refused["error"]
        # A profile saved before the removal still names it: the browser is Playwright.
        mgr.secrets.put("mcp:browser", {"backend": gone, "mode": "isolated"})
        cfg = mgr.browser_config()
        assert cfg["backend"] == "playwright" and "playwright" in cfg["launch"]
        # And the next save rewrites the profile to the one browser there is.
        assert mgr.set_browser_config({"headless": True})["ok"] is True
        assert mgr.secrets.get("mcp:browser")["backend"] == "playwright"


def test_a_save_drops_the_removed_backends_credentials(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    mgr.secrets.put(
        "mcp:browser",
        {
            "backend": "stagehand",
            "browserbase_api_key": "bb-key",
            "browserbase_project_id": "bb-proj",
            "gemini_api_key": "gm-key",
        },
    )
    out = mgr.set_browser_config({"backend": "playwright"})
    assert out["ok"] is True
    stored = mgr.secrets.get("mcp:browser")
    assert stored["backend"] == "playwright"
    assert not {"browserbase_api_key", "browserbase_project_id", "gemini_api_key"} & set(stored)
