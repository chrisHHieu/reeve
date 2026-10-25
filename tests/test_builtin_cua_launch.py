"""A file entry for computer use keeps the person's settings and runs today's launch line.

The first change to a built-in writes its whole definition into `mcp.json`, launch line included, and
a file entry replaces the built-in. One written in a dev checkout before the package was renamed kept
`python.exe -m oldpkg.cua_server`, so computer use failed to start in every session of the installed
app ("No module named 'oldpkg'", 2026-10-20).
"""

from __future__ import annotations

import pytest

from harness.extensions.mcp.config import builtin as builtin_mod
from harness.extensions.mcp.config import builtin_mcp_servers as real_builtin_mcp_servers
from harness.extensions.mcp.config import load_mcp_servers, put_global_server
from harness.interface.server.manager import SessionManager

_STALE = {
    "command": "D:\\old-checkout\\.venv\\Scripts\\python.exe",
    "args": ["-m", "oldpkg.cua_server"],
    "requires_approval": False,
}


@pytest.fixture()
def builtins(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    # conftest clears built-ins for arming tests; this file is about a built-in's file entry.
    monkeypatch.setattr("harness.extensions.mcp.config.builtin_mcp_servers", real_builtin_mcp_servers)
    return real_builtin_mcp_servers()


def _loaded(name: str):
    return next(server for server in load_mcp_servers() if server.name == name)


def test_a_stale_launch_line_runs_todays(builtins):
    put_global_server("computer-use", dict(_STALE))
    server = _loaded("computer-use")
    today = builtins["computer-use"]
    assert server.command == today["command"]
    assert list(server.args) == list(today["args"])
    assert "oldpkg" not in " ".join(server.args)


def test_the_persons_settings_in_that_entry_stay(builtins):
    put_global_server("computer-use", {**_STALE, "enabled": False})
    server = _loaded("computer-use")
    assert server.requires_approval is False
    assert server.enabled is False


@pytest.mark.parametrize(
    "launch",
    [
        {"command": "python", "args": ["C:\\old\\oldpkg\\cua_server.py"]},
        {"command": "C:\\Program Files\\Old\\reeve-cua.exe", "args": []},
        {"command": "/usr/bin/python3", "args": ["-m", "oldpkg.cua_server"]},
    ],
)
def test_every_spelling_of_reeves_own_server_is_recognised(builtins, launch):
    put_global_server("computer-use", launch)
    assert _loaded("computer-use").command == builtins["computer-use"]["command"]


def test_a_launch_line_of_the_persons_own_is_left_alone(builtins):
    put_global_server("computer-use", {"command": "my-desktop-server", "args": ["--port", "0"]})
    server = _loaded("computer-use")
    assert server.command == "my-desktop-server"
    assert list(server.args) == ["--port", "0"]


def test_a_frozen_app_without_the_exe_keeps_the_entry_as_it_is(builtins, monkeypatch):
    monkeypatch.setattr(builtin_mod, "_cua_launch", lambda: None)
    assert builtin_mod.current_launch("computer-use", dict(_STALE)) == _STALE


def test_other_servers_are_not_touched(builtins):
    entry = {"command": "python", "args": ["-m", "oldpkg.cua_server"]}
    assert builtin_mod.current_launch("browser", entry) is entry


def test_the_panel_shows_the_launch_line_that_runs(tmp_path, monkeypatch, builtins):
    put_global_server("computer-use", dict(_STALE))
    manager = SessionManager(data_dir=tmp_path / "data")
    row = next(r for r in manager.list_mcp() if r["name"] == "computer-use")
    assert row["config"]["args"] == builtins["computer-use"]["args"]
    assert row["requires_approval"] is False


# The browser's entry: an installer's Node and cli.js, in the folder the app was installed to that day.
_OLD_BROWSER = {
    "command": r"C:\Users\someone\AppData\Local\OldApp\sidecar\node\node.exe",
    "args": [
        r"C:\Users\someone\AppData\Local\OldApp\sidecar\mcp\node_modules\@playwright\mcp\cli.js",
        "--browser",
        "chromium",
        "--cdp-endpoint",
        "http://127.0.0.1:9222",
    ],
    "requires_approval": False,
}


def test_a_moved_install_runs_todays_browser_and_keeps_its_flags(builtins, monkeypatch, tmp_path):
    node, cli = tmp_path / "node.exe", tmp_path / "cli.js"
    monkeypatch.setattr(builtin_mod, "_bundled_node", lambda: (str(node), str(cli)))
    put_global_server("browser", dict(_OLD_BROWSER))
    server = _loaded("browser")
    assert server.command == str(node)
    assert list(server.args) == [str(cli), "--browser", "chromium", "--cdp-endpoint", "http://127.0.0.1:9222"]
    assert server.requires_approval is False


def test_without_a_carried_node_the_browser_runs_through_npx(builtins, monkeypatch):
    monkeypatch.setattr(builtin_mod, "_bundled_node", lambda: None)
    launch = builtin_mod.current_launch("browser", dict(_OLD_BROWSER))
    assert "@playwright/mcp@latest" in launch["args"]
    assert launch["args"][-4:] == ["--browser", "chromium", "--cdp-endpoint", "http://127.0.0.1:9222"]
    assert "OldApp" not in launch["command"] + " ".join(launch["args"])


def test_a_browser_launch_of_the_persons_own_is_left_alone(builtins):
    entry = {"command": "npx", "args": ["-y", "my-browser-mcp", "--port", "0"]}
    assert builtin_mod.current_launch("browser", entry) is entry
