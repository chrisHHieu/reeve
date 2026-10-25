"""Persistent terminals — the capability `run_shell` says it does not have.

The property every test defends: a session can notice that a program STOPPED and is
waiting, and can then be typed into. `run_shell` cannot, because it reads whole lines and a
prompt carries no newline — see harness/tools/terminal.py.
"""

from __future__ import annotations

import os
import sys

import pytest

import time

from harness.tools.registry import ToolRegistry
from harness.tools.terminal import MAX_SESSIONS, TerminalSessions, _Buffer, replace_terminal_tools, terminal_tools

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and not os.path.exists("/bin/bash"),
    reason="needs a native shell",
)

PY = sys.executable


@pytest.fixture
def sessions(tmp_path):
    store = TerminalSessions(cwd=tmp_path)
    yield store
    store.close_all()


# -- the bounded buffer ----------------------------------------------------------------


def test_the_buffer_hands_out_each_chunk_once():
    buf = _Buffer()
    buf.append("one ")
    assert buf.drain() == ("one ", 0)
    buf.append("two")
    assert buf.drain() == ("two", 0)
    assert buf.drain() == ("", 0)


def test_dropped_output_is_counted_not_hidden(monkeypatch):
    """Output silently cut is output the reader reasons about as though it were whole."""
    import harness.tools.terminal as module

    monkeypatch.setattr(module, "MAX_SCROLLBACK", 10)
    buf = _Buffer()
    buf.append("x" * 30)

    text, lost = buf.drain()
    assert len(text) == 10
    assert lost == 20


def test_the_buffer_retains_the_tail_across_many_small_chunks(monkeypatch):
    """The hot path appends and evicts chunks, never re-slices one string (measured
    58x on a 5 MiB stream); the retained tail and the loss count must be unchanged."""
    import harness.tools.terminal as module

    monkeypatch.setattr(module, "MAX_SCROLLBACK", 10)
    buf = _Buffer()
    for chunk in ("abc", "def", "ghi", "jkl"):
        buf.append(chunk)

    assert buf.snapshot() == "cdefghijkl"
    text, lost = buf.drain()
    assert text == "cdefghijkl"
    assert lost == 2  # 12 produced, 10 retained, nothing handed out before
    assert buf.drain() == ("", 0)


# -- the mechanism ---------------------------------------------------------------------


def test_a_prompt_with_no_newline_is_seen(sessions):
    """The one that matters. `LocalExecutor` iterates `for line in stdout`, so this text
    would sit in the pipe unseen while the caller concluded nothing was happening."""
    opened = sessions.open(name="repl")
    session = sessions.get(opened["id"])

    out = session.send(
        f'{PY} -c "import sys; sys.stdout.write(\'Name: \'); sys.stdout.flush(); '
        f"x = input(); print('hi ' + x)\"",
        timeout=20,
    )

    assert "Name:" in out["output"]
    # Output stopped with no newline in sight: the session is waiting to be typed into.
    assert out["reason"] == "idle"


def test_the_session_can_be_answered(sessions):
    """A capability `run_shell` states it does not have: 'nothing here can answer it'."""
    opened = sessions.open()
    session = sessions.get(opened["id"])
    session.send(
        f'{PY} -c "x = input(\'Name: \'); print(\'hello \' + x)"',
        timeout=20,
    )

    answered = session.send("world", timeout=20)

    assert "hello world" in answered["output"]


def test_state_survives_across_sends(sessions):
    """The reason a session exists at all: a REPL that forgets between calls is not a REPL."""
    opened = sessions.open()
    session = sessions.get(opened["id"])

    marker = "TERMTEST_VALUE"
    setter = f"$env:{marker}='kept'" if sys.platform == "win32" else f"export {marker}=kept"
    reader = f"echo $env:{marker}" if sys.platform == "win32" else f'echo "${marker}"'
    session.send(setter, timeout=20)

    out = session.send(reader, timeout=20)

    assert "kept" in out["output"]


def test_two_terminals_do_not_share_state(sessions):
    """One shell could not hold a REPL and a build at the same time."""
    first = sessions.get(sessions.open(name="a")["id"])
    second = sessions.get(sessions.open(name="b")["id"])
    marker = "TERMTEST_ISOLATED"
    setter = f"$env:{marker}='one'" if sys.platform == "win32" else f"export {marker}=one"
    reader = f"echo $env:{marker}" if sys.platform == "win32" else f'echo "${marker}"'
    first.send(setter, timeout=20)

    out = second.send(reader, timeout=20)

    assert "one" not in out["output"]


def test_a_send_that_outlives_its_budget_returns_without_killing_the_session(sessions):
    """A command running longer than one send is not a failure — that is what a session is
    for. The send reports `timeout` and the terminal keeps going."""
    opened = sessions.open()
    session = sessions.get(opened["id"])

    out = session.send(f'{PY} -c "import time; time.sleep(5)"', timeout=1.0)

    # NOT `idle`: it printed nothing, and silence from the start is evidence of nothing.
    # Reading it as a prompt would make every send of a slow build come back within a
    # second claiming it had been asked a question.
    assert out["reason"] == "timeout"
    assert out["running"] is True


def test_a_command_that_works_quietly_then_speaks_is_not_a_prompt(sessions):
    """The other side of the same rule: output arriving late still ends the wait normally
    rather than being missed because the session gave up on it."""
    opened = sessions.open()
    session = sessions.get(opened["id"])

    program = "import time; time.sleep(1.2); print('late-marker')"
    out = session.send(f'{PY} -c "{program}"', timeout=20)

    assert "late-marker" in out["output"]
    assert out["reason"] == "idle"


def test_an_exited_shell_reports_its_code(sessions):
    opened = sessions.open()
    session = sessions.get(opened["id"])

    session.send("exit 7", timeout=10)
    status = session.status()

    assert status["running"] is False
    assert status.get("exit_code") == 7


def test_reading_pages_the_scrollback_newest_first(sessions):
    """The `terminal_read` contract: newest-relative offset, a bounded page, and a
    named line window. Reading is stateless — the same page twice is the same text, not
    an emptied cursor."""
    opened = sessions.open()
    session = sessions.get(opened["id"])
    session.send("echo page-one; echo page-two; echo page-three", timeout=20)

    tail = session.read(count=2)
    assert "page-three" in tail["output"] and "page-two" in tail["output"]
    assert "page-one" not in tail["output"]
    assert tail["line_end"] == tail["total_lines"]  # offset 0 is the tail
    assert tail["line_begin"] == tail["total_lines"] - 2

    above = session.read(offset=2, count=2)
    assert "page-one" in above["output"]
    assert above["line_end"] == tail["line_begin"]  # walking up the log

    assert session.read(count=2)["output"] == tail["output"]


def test_sigint_stops_the_running_command_without_ending_the_shell(sessions):
    opened = sessions.open()
    session = sessions.get(opened["id"])
    session.send(f'{PY} -c "import time; time.sleep(30)"', timeout=1.5)
    assert session.running is True

    out = session.signal("SIGINT")

    assert "error" not in out, out
    assert out["signal"] == "SIGINT"
    assert out["reason"] == "signal"
    if sys.platform != "win32":
        # Ctrl-C is a request, not a session end: the shell takes the next command.
        # (Windows' CTRL_BREAK semantics differ enough that this half is POSIX-pinned.)
        check = session.send("echo still-alive", timeout=20)
        assert "still-alive" in check["output"]


def test_sigkill_on_the_shell_is_refused(sessions):
    """Ending the session is terminal_close's job: a killed shell silently
    takes its scrollback, and its children, with it."""
    opened = sessions.open()
    session = sessions.get(opened["id"])

    refused = session.signal("SIGKILL")

    assert "refusing SIGKILL" in refused["error"]
    assert session.running is True


def test_an_unknown_signal_is_refused_by_name(sessions):
    opened = sessions.open()
    session = sessions.get(opened["id"])

    refused = session.signal("SIGBOGUS")

    assert "unknown signal" in refused["error"]


def test_a_pty_terminal_is_a_real_terminal(sessions):
    opened = sessions.open(name="pty", type="pty")
    assert "error" not in opened, opened
    assert opened["type"] == "pty"
    session = sessions.get(opened["id"])

    out = session.send("echo pty-marker", timeout=20)

    assert "pty-marker" in out["output"]
    # The model gets what is on the screen — PSReadLine's redraw escapes are stripped.
    assert "\x1b" not in out["output"]
    assert session.running is True
    # ...but the buffer keeps the raw stream (a panel subscriber reads this).
    assert "\x1b" in session.snapshot()["output"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="ConPTY has no foreground-pgid query"
)
def test_a_pty_names_stdin_read_when_a_child_owns_the_foreground(sessions):
    """The claim a pipe cannot make: a PTY's `tcgetpgrp` proves a child owns the
    terminal, so a quiet send that asked a question says `stdin_read`, not `idle`."""
    opened = sessions.open(type="pty")
    session = sessions.get(opened["id"])

    out = session.send(f'{PY} -c "x = input(\'Name: \')"', timeout=20)

    assert "Name:" in out["output"]
    assert out["reason"] == "stdin_read"


def test_an_unknown_terminal_type_is_refused(sessions):
    refused = sessions.open(type="vt100")

    assert "unknown terminal type" in refused["error"]


def test_foreground_is_child_compares_the_pgids(monkeypatch):
    """The stdin_read evidence, pinned without a POSIX machine: a foreground pgid that
    differs from the shell's is the child owning the terminal; equal or unknown is not."""
    from harness.tools import pty_session as module

    probe = module.PtySession.__new__(module.PtySession)
    monkeypatch.setattr(module.PtySession, "foreground_pgid", lambda self: 120)
    monkeypatch.setattr(module.PtySession, "shell_pgid", lambda self: 100)
    assert module.PtySession.foreground_is_child(probe) is True

    monkeypatch.setattr(module.PtySession, "foreground_pgid", lambda self: 100)
    assert module.PtySession.foreground_is_child(probe) is False

    monkeypatch.setattr(module.PtySession, "foreground_pgid", lambda self: None)
    assert module.PtySession.foreground_is_child(probe) is False


def test_close_kills_the_shells_children_too(tmp_path):
    """A shell's terminate() kills the shell, not the dev server it started. The close
    walks the tree and waits for it to empty — the marker a surviving child would write
    must never appear."""
    store = TerminalSessions(cwd=tmp_path)
    session = store.get(store.open()["id"])
    marker = tmp_path / "child-alive.txt"
    script = tmp_path / "child.py"
    script.write_text(
        "import time\n"
        "from pathlib import Path\n"
        "time.sleep(4)\n"
        f"Path(r'{marker}').write_text('alive')\n",
        encoding="utf-8",
    )
    command = (
        f'& "{PY}" "{script}"' if sys.platform == "win32" else f'"{PY}" "{script}"'
    )
    session.send(command, timeout=1.0)
    assert session.running is True

    closed = store.close(session.id)
    time.sleep(4.5)

    assert closed["closed"] is True
    assert "warning" not in closed
    assert not marker.exists()


def test_a_child_registry_gets_its_own_terminals(tmp_path):
    """Owner isolation: rebinding the six tools to the
    child's own TerminalSessions means a parent's terminal ids are invisible there."""
    parent_store = TerminalSessions(cwd=tmp_path)
    parent_registry = ToolRegistry()
    parent_registry.register_all(terminal_tools(parent_store))
    parent_opened = parent_store.open(name="parent")

    child_store = TerminalSessions(cwd=tmp_path)
    assert replace_terminal_tools(parent_registry, child_store) is True

    names = {"terminal_list", "terminal_open"}
    tools = {name: parent_registry.get(name).func for name in names}
    assert tools["terminal_list"]()["terminals"] == []

    child_opened = tools["terminal_open"](name="child")
    assert "error" not in child_opened
    assert parent_store.get(child_opened["id"]) is None
    assert child_store.get(child_opened["id"]) is not None
    assert parent_store.get(parent_opened["id"]) is not None

    parent_store.close_all()
    child_store.close_all()


def test_replace_terminal_tools_is_a_noop_without_them(tmp_path):
    assert replace_terminal_tools(ToolRegistry(), TerminalSessions(cwd=tmp_path)) is False


# -- the registry ----------------------------------------------------------------------


def test_open_waits_for_the_shell_to_settle(sessions):
    """Returning immediately would hand back an empty terminal and let the first send race
    the shell's own startup text."""
    opened = sessions.open(name="shell")

    assert opened["running"] is True
    assert "output" in opened


def test_terminals_are_listed_and_closed_by_id(sessions):
    first = sessions.open(name="a")
    sessions.open(name="b")

    assert {t["name"] for t in sessions.list()} == {"a", "b"}
    assert sessions.close(first["id"])["closed"] is True
    assert sessions.close(first["id"])["error"].startswith("no terminal")


def test_the_number_of_live_terminals_is_bounded(sessions):
    """Each is a live process tree; an agent opening one per thought would leave them all
    running."""
    for _ in range(MAX_SESSIONS):
        assert "error" not in sessions.open()

    refused = sessions.open()

    assert "error" in refused
    assert str(MAX_SESSIONS) in refused["error"]
    assert len(refused["terminals"]) == MAX_SESSIONS


def test_a_closed_terminal_frees_its_slot(sessions):
    ids = [sessions.open()["id"] for _ in range(MAX_SESSIONS)]
    assert "error" in sessions.open()

    sessions.close(ids[0])

    assert "error" not in sessions.open()


def test_close_all_leaves_nothing_running(tmp_path):
    store = TerminalSessions(cwd=tmp_path)
    store.open()
    store.open()
    live = [store.get(t["id"]) for t in store.list()]

    store.close_all()

    assert store.list() == []
    assert all(not s.running for s in live)


# -- what the permission engine sees ---------------------------------------------------
#
# A terminal is a shell. Every gate that guards `run_shell` has to guard these too, and the
# way it goes wrong is silent: a tool that executes but is classified READ skips the
# self-protection floor, the read-only modes and the approval card in one step.


def test_the_executing_verbs_are_classified_as_execution():
    from harness.safety.risk import RiskClass, classify

    assert classify("terminal_open") is RiskClass.EXEC
    assert classify("terminal_send") is RiskClass.EXEC
    assert classify("terminal_signal") is RiskClass.EXEC
    # Reading and listing touch nothing new.
    assert classify("terminal_read") is RiskClass.READ
    assert classify("terminal_list") is RiskClass.READ


def test_the_command_argument_is_found_under_its_own_name():
    """`run_shell` calls it `command`, `terminal_send` calls it `text`. Two places inspect
    it, and a tool spelled differently would slip past both while still reading as EXEC."""
    from harness.safety.permissions import command_text

    assert command_text("run_shell", {"command": "ls"}) == "ls"
    assert command_text("terminal_send", {"text": "ls", "id": "term-1"}) == "ls"
    # Opening a shell runs nothing of its own: empty is the true answer, not a missing one.
    assert command_text("terminal_open", {"name": "build"}) == ""


def test_typing_at_reeves_own_settings_is_refused(tmp_path, monkeypatch):
    """The self-protection floor runs before mode, allowlists and every auto-allow path.
    It read `arguments["command"]`, so before the argument map this call reached the shell
    with nothing looking at what was being typed."""
    import harness.safety.boundary.geometry as boundary
    from harness.safety.permissions import Mode, PermissionEngine

    secrets = tmp_path / "secrets.json"
    secrets.write_text("{}", encoding="utf-8")
    # The floor's file list lives on the reach axis now (harness/safety/boundary.py); the
    # patch targets its home so the check reads exactly the paths this test owns.
    monkeypatch.setattr(boundary, "protected_paths", lambda: [str(secrets)])

    engine = PermissionEngine(workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS)
    decision = engine.evaluate("terminal_send", {"id": "term-1", "text": f"cat {secrets}"})

    assert decision.allowed is False
    assert decision.needs_user is False  # no click can grant it either
    assert "own settings" in decision.reason


def test_read_only_plan_stops_a_terminal_from_opening(tmp_path):
    """Plan mode is read-only, and a shell held open across calls is not a read."""
    from harness.safety.permissions import PermissionEngine

    engine = PermissionEngine(workspace_root=str(tmp_path), plan=True)

    assert engine.evaluate("terminal_open", {}).allowed is False
    assert engine.evaluate("terminal_send", {"id": "t", "text": "ls"}).allowed is False
    # ...but looking at what already happened is still a read.
    assert engine.evaluate("terminal_list", {}).allowed is True


def test_the_card_shows_what_is_being_typed(tmp_path):
    """A card reading "send to term-1a2b" asks a person to consent to a command they were
    never shown."""
    from harness.tools.contract import contract_of, safe_present_call
    from harness.tools.terminal import TerminalSessions, terminal_tools

    tools = {t.__name__: t for t in terminal_tools(TerminalSessions(cwd=tmp_path))}
    shown = safe_present_call(
        contract_of(tools["terminal_send"]),
        "terminal_send",
        {"id": "term-1", "text": "rm -rf build"},
    )

    assert shown is not None
    assert "rm -rf build" in shown.summary
