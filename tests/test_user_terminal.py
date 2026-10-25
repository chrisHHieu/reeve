"""The user's terminal panel (`/terminal`) — its own shell, its own wire.

The property that matters: the panel's shell is a SEPARATE process from the agent's
terminals, so opening the panel can never drain output the model's `terminal_read` was
going to see (and vice versa). These tests pin the manager's attach/fan-out semantics
with a fake shell, and the REST + WS routes against a real one where a native shell
exists.
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app


def _manager(tmp_path):
    return SessionManager(workspace=tmp_path)


# -- manager semantics, with a fake shell ---------------------------------------------


class FakeShell:
    """Stands in for PtySession: records writes, emits output on demand."""

    instances: list["FakeShell"] = []

    def __init__(
        self, *, session_id, cwd, name="", env=None, shell_path=None, on_output=None, cols=None, rows=None, marks=False
    ):
        self.id = session_id
        self.marks = marks
        self.cwd = str(cwd)
        self.name = name
        self.shell_path = shell_path or "fake"
        self.cols = cols or 120
        self.rows = rows or 30
        self.running = True
        self.written: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self._on_output = on_output
        self._produced = 0
        FakeShell.instances.append(self)

    def snapshot(self):
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "shell": self.shell_path,
            "running": self.running,
            "output": "",
            "produced": self._produced,
        }

    def write(self, text):
        self.written.append(text)
        return {"ok": True, "running": True}

    def interrupt(self):
        return {"ok": True, "interrupted": True}

    def resize(self, cols, rows):
        self.resizes.append((cols, rows))
        return {"ok": True, "cols": cols, "rows": rows}

    def close(self):
        self.running = False
        return {"id": self.id, "closed": True}

    def emit(self, text):
        start, end = self._produced, self._produced + len(text)
        self._produced = end
        if self._on_output is not None:
            self._on_output(text, start, end)


@pytest.fixture
def fake_shells(monkeypatch):
    import harness.interface.server.manager.user_terminal as module

    FakeShell.instances = []
    monkeypatch.setattr(module, "PtySession", FakeShell)
    return FakeShell


def test_user_terminal_is_one_shell_per_session(tmp_path, fake_shells):
    manager = _manager(tmp_path)
    first = manager.user_terminal("s1")
    second = manager.user_terminal("s1")
    other = manager.user_terminal("s2")

    assert first["ok"] and second["ok"] and other["ok"]
    assert len(fake_shells.instances) == 2
    assert first["id"] == "user-s1-1"
    assert other["id"] == "user-s2-1"
    # The person's shell is the one that marks its commands.
    assert all(shell.marks is True for shell in fake_shells.instances)


def test_attach_subscribes_then_delivers_with_positions(tmp_path, fake_shells):
    manager = _manager(tmp_path)
    seen: list[tuple[str, int, int]] = []

    def subscriber(chunk, start, end):
        seen.append((chunk, start, end))

    info = manager.user_terminal_attach("s1", subscriber)
    assert info["ok"]
    shell = fake_shells.instances[0]

    shell.emit("hello")
    assert seen == [("hello", 0, 5)]

    manager.user_terminal_unsubscribe("s1", subscriber)
    shell.emit("more")
    # Unsubscribed: no new delivery. (The chunk still landed in the shell's own buffer.)
    assert seen == [("hello", 0, 5)]


def test_write_interrupt_and_close_forward(tmp_path, fake_shells):
    manager = _manager(tmp_path)
    manager.user_terminal("s1")
    shell = fake_shells.instances[0]

    assert manager.user_terminal_write("s1", "echo hi\n")["ok"]
    assert shell.written == ["echo hi\n"]
    assert manager.user_terminal_interrupt("s1")["interrupted"] is True

    assert manager.user_terminal_close("s1") == {"ok": True, "closed": True}
    assert shell.running is False
    # A closed shell is replaced on the next attach rather than reported dead.
    again = manager.user_terminal("s1")
    assert again["ok"] and again["id"] == "user-s1-1"
    assert len(fake_shells.instances) == 2


def test_user_terminal_never_touches_engine_terminals(tmp_path, fake_shells):
    """The panel's own shell: the agent's `terminals` registry stays untouched."""
    manager = _manager(tmp_path)
    manager.user_terminal("s1")
    assert manager.user_terminal("s1")["ok"]
    assert "s1" not in manager._engines


def test_invalid_session_id_is_refused(tmp_path, fake_shells):
    manager = _manager(tmp_path)
    assert manager.user_terminal("")["ok"] is False
    assert manager.user_terminal("..")["ok"] is False
    assert manager.user_terminal("a/b")["ok"] is False


def test_shell_argv_is_keyed_on_the_executable():
    from harness.tools.terminal import shell_argv

    # PowerShell (and pwsh) keep the -Command - stdin loop…
    assert shell_argv("powershell.exe", is_windows=True) == [
        "powershell.exe",
        "-NoProfile",
        "-NoLogo",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "-",
    ]
    assert shell_argv(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", is_windows=True
    )[1:3] == ["-NoProfile", "-NoLogo"]
    # …cmd would choke on those flags: it reads lines from the pipe and /K keeps it alive.
    assert shell_argv("cmd.exe", is_windows=True) == ["cmd.exe", "/Q", "/K"]
    # A POSIX shell (or Git Bash on Windows) is interactive.
    assert shell_argv("/bin/bash", is_windows=False) == ["/bin/bash", "-i"]
    assert shell_argv(r"C:\Program Files\Git\bin\bash.exe", is_windows=True)[-1] == "-i"


def test_shell_menu_lists_only_installed_shells(tmp_path, fake_shells, monkeypatch):
    import harness.interface.server.manager.user_terminal as module

    monkeypatch.setattr(module.shutil, "which", lambda path: f"/found/{path}")
    manager = _manager(tmp_path)
    listed = manager.user_terminal_shells("s1")
    assert listed["ok"] is True
    expected = ["cmd", "bash", "powershell"] if os.name == "nt" else ["bash", "sh"]
    assert [shell["id"] for shell in listed["shells"]] == expected
    assert all(shell["path"].startswith("/found/") for shell in listed["shells"])

    # What the machine does not have is not offered.
    monkeypatch.setattr(module.shutil, "which", lambda path: None)
    assert manager.user_terminal_shells("s1")["shells"] == []


def test_choosing_a_shell_starts_it_and_switching_replaces_the_running_one(
    tmp_path, fake_shells, monkeypatch
):
    import harness.interface.server.manager.user_terminal as module

    monkeypatch.setattr(
        module,
        "_resolve_shell",
        lambda shell_id: f"resolved/{shell_id}" if shell_id in {"cmd", "powershell"} else None,
    )
    manager = _manager(tmp_path)
    first = manager.user_terminal("s1", shell="cmd")
    assert first["ok"] is True
    assert first["shell"] == "resolved/cmd"
    assert len(fake_shells.instances) == 1

    # The same interpreter attaches instead of churning (the menu's main click).
    again = manager.user_terminal("s1", shell="cmd")
    assert again["ok"] is True
    assert len(fake_shells.instances) == 1

    # A different pick ends the old shell and starts the requested interpreter.
    switched = manager.user_terminal("s1", shell="powershell")
    assert switched["ok"] is True
    assert switched["shell"] == "resolved/powershell"
    assert len(fake_shells.instances) == 2
    assert fake_shells.instances[0].running is False

    unknown = manager.user_terminal("s1", shell="nope")
    assert unknown["ok"] is False
    assert "nope" in unknown["error"]


def test_multiple_terminals_per_session_are_independent(tmp_path, fake_shells):
    manager = _manager(tmp_path)
    first = manager.user_terminal("s1", terminal_id="t1")
    second = manager.user_terminal("s1", terminal_id="t2")
    assert first["ok"] and second["ok"]
    assert first["id"] == "user-s1-t1"
    assert second["id"] == "user-s1-t2"
    assert len(fake_shells.instances) == 2

    assert manager.user_terminal_write("s1", "echo one\n", "t1")["ok"]
    assert fake_shells.instances[0].written == ["echo one\n"]
    assert fake_shells.instances[1].written == []

    # Resizing one tab does not touch the other.
    assert manager.user_terminal_resize("s1", 100, 20, "t1")["ok"]
    assert fake_shells.instances[0].resizes == [(100, 20)]
    assert fake_shells.instances[1].resizes == []

    assert manager.user_terminal_close("s1", "t1") == {"ok": True, "closed": True}
    assert len(manager._user_shells) == 1

    refused = manager.user_terminal("s1", terminal_id="bad id!")
    assert refused["ok"] is False
    assert "terminal id" in refused["error"]


# -- the routes, against a real shell --------------------------------------------------

pytestmark_shell = pytest.mark.skipif(
    sys.platform != "win32" and not os.path.exists("/bin/bash"),
    reason="needs a native shell",
)


@pytestmark_shell
def test_rest_open_status_close(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    try:
        opened = client.post("/v1/sessions/s1/terminal").json()
        assert opened["ok"] is True
        assert opened["id"] == "user-s1-1"
        assert opened["running"] is True
        assert opened["cwd"] == str(tmp_path)

        status = client.get("/v1/sessions/s1/terminal").json()
        assert status["ok"] is True and status["running"] is True

        closed = client.delete("/v1/sessions/s1/terminal").json()
        assert closed == {"ok": True, "closed": True}
        assert client.delete("/v1/sessions/s1/terminal").json() == {"ok": True, "closed": False}
    finally:
        manager.close_user_terminals()


@pytestmark_shell
def test_rest_shell_menu_lists_and_picks(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    try:
        listed = client.get("/v1/sessions/s1/terminal/shells").json()
        assert listed["ok"] is True and listed["shells"]
        pick = listed["shells"][0]
        assert pick["id"] and pick["name"] and pick["path"]

        opened = client.post("/v1/sessions/s1/terminal", json={"shell": pick["id"]}).json()
        assert opened["ok"] is True
        assert opened["running"] is True
        assert opened["shell"] == pick["path"]

        refused = client.post("/v1/sessions/s1/terminal", json={"shell": "no-such-shell"}).json()
        assert refused["ok"] is False and "no-such-shell" in refused["error"]
    finally:
        manager.close_user_terminals()


@pytestmark_shell
def test_ws_attaches_streams_input_and_exit(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    try:
        with client.websocket_connect("/ws/terminal/s1") as ws:
            attach = ws.receive_json()
            assert attach["type"] == "terminal"
            assert attach["data"]["ok"] is True
            assert attach["data"]["session_id"] == "s1"
            assert attach["data"]["running"] is True

            ws.send_json({"type": "input", "data": "echo panel-alive\n"})
            text = ""
            deadline = time.monotonic() + 20
            while "panel-alive" not in text and time.monotonic() < deadline:
                message = ws.receive_json()
                if message["type"] == "output":
                    text += message["data"]["chunk"]
            assert "panel-alive" in text
    finally:
        manager.close_user_terminals()
