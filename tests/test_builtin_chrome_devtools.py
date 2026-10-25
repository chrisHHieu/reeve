"""Chrome DevTools MCP is a built-in server, beside `browser` and `computer-use`, off until switched on.

It left as a backend of `browser` on 2026-09-30 and a person who had mounted it themselves found it
gone from the panel. It is back as its own server: its own name (`chrome-devtools`, so its tools never
collide with `browser`'s), shipped OFF because it runs through `npx` and nothing bundles Node for it
(a person without Node would be told it failed to start in every session), and switched on from the
same panel as any built-in. What has to hold:

  - it is in the built-in set, disabled, per session, launched through npx with the privacy flags that
    stop Google's usage statistics and CrUX lookups, and follows the Browser settings (headless, attach);
  - disabled means no session arms it, and a person's own `mcp.json` entry replaces it wholesale;
  - the panel lists it as built-in and disabled; the first switch-on writes the full definition, so the
    file entry is a complete server and not a fragment;
  - the Browser settings still drive `browser`, which is untouched.
"""

from __future__ import annotations

import pytest

from harness.extensions.mcp.config import builtin_mcp_servers as real_builtin_mcp_servers
from harness.extensions.mcp.config import load_mcp_servers, read_global
from harness.interface.server.manager import SessionManager

_BROWSER_ENV = (
    "REEVE_BROWSER_BACKEND",
    "REEVE_BROWSER_MODE",
    "REEVE_BROWSER_ENDPOINT",
    "REEVE_BROWSER_HEADLESS",
)


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    for key in _BROWSER_ENV:
        monkeypatch.delenv(key, raising=False)
    # conftest clears built-ins for arming tests; this file is about the built-in set itself.
    monkeypatch.setattr("harness.extensions.mcp.config.builtin_mcp_servers", real_builtin_mcp_servers)
    return SessionManager(data_dir=tmp_path / "data")


def test_it_is_a_built_in_that_ships_off_and_per_session():
    servers = real_builtin_mcp_servers()
    assert {"browser", "computer-use", "chrome-devtools"} <= set(servers)
    devtools = servers["chrome-devtools"]
    assert devtools["enabled"] is False and devtools["per_session"] is True
    assert "chrome-devtools-mcp@latest" in devtools["args"] and "npx" in " ".join([devtools["command"], *devtools["args"]])
    assert servers["browser"].get("enabled", True) is True  # the others are untouched: on, as before
    assert servers["computer-use"].get("enabled", True) is True


def test_what_leaves_the_machine_is_switched_off():
    args = real_builtin_mcp_servers()["chrome-devtools"]["args"]
    assert "--no-usage-statistics" in args  # Google's usage collection
    assert "--no-performance-crux" in args  # a performance trace would send the page's URL to CrUX


def test_it_runs_isolated_and_headless_by_default_like_the_browser():
    args = real_builtin_mcp_servers()["chrome-devtools"]["args"]
    assert "--isolated" in args and "--headless=true" in args
    assert "--browser-url" not in args and "--ws-endpoint" not in args


def test_the_browser_settings_drive_it_too(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REEVE_BROWSER_HEADLESS", "0")
    visible = real_builtin_mcp_servers()["chrome-devtools"]["args"]
    assert "--headless=false" in visible

    monkeypatch.setenv("REEVE_BROWSER_MODE", "attach")
    monkeypatch.setenv("REEVE_BROWSER_ENDPOINT", "http://127.0.0.1:9222")
    attached = real_builtin_mcp_servers()["chrome-devtools"]["args"]
    assert attached[attached.index("--browser-url") + 1] == "http://127.0.0.1:9222"
    assert "--isolated" not in attached

    monkeypatch.setenv("REEVE_BROWSER_ENDPOINT", "ws://127.0.0.1:9222/devtools/browser/abc")
    websocket = real_builtin_mcp_servers()["chrome-devtools"]["args"]
    assert websocket[websocket.index("--ws-endpoint") + 1].startswith("ws://")
    assert "--browser-url" not in websocket


def test_a_disabled_built_in_is_loaded_but_no_session_arms_it(manager, tmp_path):
    loaded = {s.name: s for s in load_mcp_servers(secrets=manager.secrets)}
    assert loaded["chrome-devtools"].enabled is False and loaded["chrome-devtools"].per_session is True
    plan = manager._mcp_plan("s1", str(tmp_path), "reeve")
    assert "chrome-devtools" not in {s.name for s in plan}
    assert "browser" in {s.name for s in plan}  # the browser is on and still armed


def test_a_users_own_entry_replaces_the_built_in_wholesale(manager, tmp_path):
    from harness.extensions.mcp.config import put_global_server

    put_global_server("chrome-devtools", {"command": "my-devtools", "args": ["--mine"], "enabled": True})
    loaded = {s.name: s for s in load_mcp_servers(secrets=manager.secrets)}
    assert loaded["chrome-devtools"].command == "my-devtools" and loaded["chrome-devtools"].args == ["--mine"]
    assert "chrome-devtools" in {s.name for s in manager._mcp_plan("s1", str(tmp_path), "reeve")}


def test_the_panel_lists_it_as_a_built_in_that_is_off(manager):
    listed = {s["name"]: s for s in manager.list_mcp()}
    entry = listed["chrome-devtools"]
    assert entry["builtin"] is True and entry["enabled"] is False and entry["status"] == "disabled"
    assert listed["browser"]["enabled"] is True and listed["browser"]["status"] == "configured"


def test_the_first_switch_on_writes_the_whole_definition_and_arms_it(manager, tmp_path):
    assert "chrome-devtools" not in read_global()
    assert manager.patch_mcp("chrome-devtools", {"enabled": True}) == {"ok": True, "name": "chrome-devtools"}
    written = read_global()["chrome-devtools"]
    assert written["enabled"] is True and written["per_session"] is True
    assert "--no-usage-statistics" in written["args"] and "--isolated" in written["args"]  # not a bare fragment

    listed = {s["name"]: s for s in manager.list_mcp()}["chrome-devtools"]
    assert listed["enabled"] is True and listed["status"] == "configured"
    assert "chrome-devtools" in {s.name for s in manager._mcp_plan("s1", str(tmp_path), "reeve")}

    manager.patch_mcp("chrome-devtools", {"enabled": False})  # and back off
    assert "chrome-devtools" not in {s.name for s in manager._mcp_plan("s1", str(tmp_path), "reeve")}


def test_each_session_gets_its_own_browser_and_the_workspace_is_its_cwd(manager, tmp_path):
    manager.patch_mcp("chrome-devtools", {"enabled": True})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    server = next(s for s in manager._mcp_plan("s1", str(workspace), "reeve") if s.name == "chrome-devtools")
    assert server.per_session is True and server.cwd == str(workspace)


def test_changing_the_browser_settings_releases_a_live_devtools_browser_too(manager, monkeypatch):
    released = []
    monkeypatch.setattr(manager.mcp, "forget", lambda name, *a, **k: released.append(name))
    manager.set_browser_config({"mode": "isolated", "headless": False})
    assert "browser" in released and "chrome-devtools" in released
