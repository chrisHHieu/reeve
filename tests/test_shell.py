"""The shell (`tools/shell`): one process per command in the system's own shell (PowerShell on
Windows, bash elsewhere), so every command below is written once per shell.

What holds in both: the working directory carries over and nothing else does, an exit code is
reported as it was, a deadline ends the wait and not the command, long output keeps both ends, a
background task is read and stopped by its id, and a command that waits for input is said to.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from harness.safety.permissions import PermissionEngine
from harness.tools import ToolRegistry
from harness.tools.shell import LocalExecutor, shell_tools

_WIN = sys.platform == "win32"


def _sh(powershell: str, posix: str) -> str:
    return powershell if _WIN else posix


SET_ENV = _sh("$env:GREETING='hello_world'", "export GREETING=hello_world")
ECHO_ENV = _sh("echo $env:GREETING", "echo $GREETING")
EXIT_OK = _sh("cmd /c exit 0", "true")
EXIT_FAIL = _sh("cmd /c exit 1", "false")
SLEEP_5 = _sh("Start-Sleep -Seconds 5", "sleep 5")
PRINT_1000 = _sh('foreach ($i in 1..1000) { "line$i" }', "for i in $(seq 1 1000); do echo line$i; done")
QUICK_ECHO = _sh("Write-Output quick_done", "echo quick_done")
ECHO_THEN_SLEEP = _sh("Write-Output started; Start-Sleep -Seconds 30", "echo started; sleep 30")
# A command that waits for something to be typed. Two shapes, and the difference decides what the
# executor can honestly say: one echoes the line it swallowed, one does not.
PROMPT_ECHOES = _sh('$a = Read-Host "Q"; Write-Output "after:$a"', 'read a; echo "after:$a"')
PROMPT_SILENT = _sh('$a = Read-Host "Q"', "read a")
# Prints a marker, then holds past any short timeout: the shape of the live bug, a command that had
# already stopped both servers when the executor gave up on it.
TIMEOUT_AFTER_PRINT = _sh('Write-Output "=== stop servers ==="; Start-Sleep -Seconds 5', 'echo "=== stop servers ==="; sleep 5')


@pytest.fixture
def executor(tmp_path):
    shell = LocalExecutor(cwd=tmp_path, default_timeout=10)
    yield shell
    shell.close()


def _registry(executor) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(shell_tools(executor))
    return registry


def test_cwd_persists_across_calls(executor, tmp_path):
    (tmp_path / "sub").mkdir()
    executor.run("cd sub")
    where = executor.run("pwd")
    assert where["exit_code"] == 0 and "sub" in where["output"]
    assert executor.cwd.endswith("sub")


def test_env_does_not_persist_across_calls(executor):
    """One process per command: what `export` or `$env:` set is gone by the next
    command. The working directory is the one thing that carries over (the trailer brings it back,
    `test_cwd_persists_across_calls`)."""
    assert executor.run(SET_ENV)["exit_code"] == 0
    echoed = executor.run(ECHO_ENV)
    assert echoed["exit_code"] == 0 and "hello_world" not in echoed["output"]


def test_managed_bin_dir_is_on_path_from_spawn(tmp_path, monkeypatch):
    """A tool approved in the middle of a session works by name in the shell already running
    (owner-hit 2026-08-14: a freshly installed trivy needed its full path). The bin folder is on
    PATH from the start, before anything is in it."""
    from harness.tools import toolchain

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    shell = LocalExecutor(cwd=tmp_path, default_timeout=10)
    try:
        path = shell._env["PATH"]
        assert str(toolchain.bin_dir()) in path.split(os.pathsep)
        assert not path.startswith(str(toolchain.bin_dir()))  # the person's own copies come first
    finally:
        shell.close()


def test_exit_code_captured(executor):
    assert (executor.run(EXIT_OK)["exit_code"], executor.run(EXIT_FAIL)["exit_code"]) == (0, 1)


def test_timeout_moves_the_command_to_the_background(executor):
    """The deadline ends the wait, not the command: it runs on, can still be read, and
    the session is woken when it ends."""
    began = time.monotonic()
    waited = executor.run(SLEEP_5, timeout=1)
    assert time.monotonic() - began < 4.0  # not the whole sleep
    assert waited["timed_out"] is True and waited["status"] == "background" and waited["task_id"]
    assert executor.background_output(waited["task_id"])["status"] == "running"  # alive, readable
    executor.background_kill(waited["task_id"])
    assert executor.run("echo alive")["output"].strip().endswith("alive")  # and the shell is free


def test_an_interrupt_kills_the_command_and_leaves_the_executor_usable(tmp_path):
    """The user's Stop IS a kill (a timeout only ends the wait): the command is stopped,
    the result says so, and the next command runs. A Stop that arrived while nothing was
    in flight must not poison that next command."""
    import threading

    ex = LocalExecutor(cwd=tmp_path, default_timeout=30)
    result: dict = {}
    try:
        runner = threading.Thread(
            target=lambda: result.update(ex.run(SLEEP_5, timeout=30))
        )
        runner.start()
        time.sleep(0.4)
        ex.interrupt_now()
        runner.join(timeout=15)
        assert not runner.is_alive(), "the wait loop did not notice the interrupt"
        assert result.get("error") == "interrupted by user"
        assert result.get("status") != "background"

        # Still usable — and not instantly killed by the stale flag.
        after = ex.run("echo still_alive", timeout=10)
        assert after["exit_code"] == 0
        assert "still_alive" in after["output"]
    finally:
        ex.close()


def test_large_output_truncated_keeps_both_ends(tmp_path):
    """The tail gets the larger share, since test and build verdicts are at the end, but the head
    holds what ran and the first error, and which one matters depends on the output, not the tool.
    The cut in the middle says how much it left out, so the model sees the hole."""
    shell = LocalExecutor(cwd=tmp_path, max_output_chars=200, default_timeout=10)
    try:
        printed = shell.run(PRINT_1000)
        output = printed["output"]
        assert printed["truncated"] is True and printed["omitted_lines"] > 0
        assert len(output) <= 200  # the marker counts too: 200 characters is a promise about the context
        assert output.startswith("line1") and "line1000" in output and "omitted from the middle" in output
    finally:
        shell.close()


def test_shell_tool_integration(executor, tmp_path):
    registry = _registry(executor)
    assert {"run_shell", "shell_task_output", "shell_task_kill"} <= set(registry.names())
    run = registry.get("run_shell").metadata
    assert run.requires_approval is True
    # reading or stopping the agent's own background task asks nobody
    assert registry.get("shell_task_output").metadata.requires_approval is False
    assert registry.get("shell_task_kill").metadata.requires_approval is False
    permissions = PermissionEngine(workspace_root=tmp_path)
    install = permissions.evaluate("run_shell", {"command": "npm install"}, run)
    assert not install.allowed and install.needs_user
    assert permissions.evaluate("run_shell", {"command": "echo hi"}, run).allowed  # a read runs unasked
    assert "hi" in registry.execute("run_shell", {"command": "echo hi"})["output"]


def test_run_shell_accepts_description_and_clamped_timeout(executor):
    # `description` is for the approval prompt and the audit; the timeout is clamped, not refused.
    ran = _registry(executor).execute("run_shell", {"command": "echo ok", "description": "Say ok", "timeout_seconds": 99999})
    assert ran["exit_code"] == 0 and "ok" in ran["output"]


# -- a task left running in the background ------------------------------------------


def _poll_output(reg, task_id, *, until_status=None, deadline=10.0):
    """Reads `shell_task_output` until the task reaches `until_status` (or, with none, until it has
    printed something), then reads on until two reads in a row bring nothing. The status can turn
    final on the same tick its last output is still being drained into the task's buffer."""
    output, read = "", {}
    stop = time.monotonic() + deadline
    while time.monotonic() < stop:
        read = reg.execute("shell_task_output", {"task_id": task_id})
        output += read["output"]
        reached = read["status"] == until_status if until_status else bool(output)
        if not reached:
            time.sleep(0.1)
            continue
        if until_status:
            quiet, drain_until = 0, time.monotonic() + 0.5
            while quiet < 2 and time.monotonic() < drain_until:
                more = reg.execute("shell_task_output", {"task_id": task_id})
                if more["output"]:
                    output, read, quiet = output + more["output"], more, 0
                else:
                    quiet += 1
                    time.sleep(0.02)
        return output, read
    return output, read


def test_a_non_ascii_command_round_trips(executor):
    """PowerShell 5.1 speaks the OEM code page (437 on a US box) on redirected handles. A
    Vietnamese command used to die in our own `stdin.write` (cp1252 UnicodeEncodeError — 4
    real sessions) and, once the write was allowed, the shell silently compared mojibake.
    The command now travels as argv (Unicode), and the console preamble fixes the OUTPUT
    side, which is the half that still goes through a code page."""
    if _WIN:
        result = executor.run("Write-Output 'ăn uống → ầ'")
    else:
        result = executor.run("printf '%s\\n' 'ăn uống → ầ'")
    assert result["exit_code"] == 0
    assert "ăn uống → ầ" in result["output"]


def test_a_non_ascii_pattern_matches_a_utf8_file(executor, tmp_path):
    """The semantics, not just the round trip: a mojibake pattern returns false with no
    error, which is worse than the crash it replaced."""
    (tmp_path / "note.txt").write_text("món ăn uống → ầ\n", encoding="utf-8")
    if _WIN:
        cmd = (
            "$c = Get-Content -Raw -Encoding UTF8 note.txt; "
            "if ($c -match 'ăn uống') { 'MATCH' } else { 'MISS' }"
        )
    else:
        cmd = "grep -q 'ăn uống' note.txt && echo MATCH || echo MISS"
    result = executor.run(cmd)
    assert "MATCH" in result["output"]


def test_a_python_child_can_print_non_ascii(executor, tmp_path):
    """`PYTHONIOENCODING`: without it the child encodes its stdout with the locale codec and
    dies on the first Vietnamese character — the exact failure four sessions hit."""
    (tmp_path / "hello.py").write_text("print('ăn uống → ầ')\n", encoding="utf-8")
    quoted = f'"{sys.executable}"' if _WIN else sys.executable
    call = f"& {quoted} hello.py" if _WIN else f"{quoted} hello.py"
    result = executor.run(call)
    assert result["exit_code"] == 0, result["output"]
    assert "ăn uống → ầ" in result["output"]


def test_a_python_child_reads_and_writes_files_as_utf8(executor, tmp_path):
    """`PYTHONUTF8`: PYTHONIOENCODING covers stdio only — `open()` / `read_text()` without an
    encoding still used the locale codec (cp1252), so a script reading a UTF-8 file died
    with UnicodeDecodeError (session 3d9041d1, after the stdio fix) and one writing
    Vietnamese died with UnicodeEncodeError."""
    (tmp_path / "in.txt").write_text("món ăn uống → ầ\n", encoding="utf-8")
    (tmp_path / "rw.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text(Path('in.txt').read_text().upper())\n"
        "print(open('out.txt').read().strip())\n",
        encoding="utf-8",
    )
    quoted = f'"{sys.executable}"' if _WIN else sys.executable
    call = f"& {quoted} rw.py" if _WIN else f"{quoted} rw.py"
    result = executor.run(call)
    assert result["exit_code"] == 0, result["output"]
    assert "MÓN ĂN UỐNG → Ầ" in result["output"]
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "MÓN ĂN UỐNG → Ầ"


def test_a_background_task_round_trips_non_ascii(executor):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    command = (
        "Write-Output 'ăn uống →'" if _WIN else "printf '%s\\n' 'ăn uống →'"
    )
    started = reg.execute("run_shell", {"command": command, "run_in_background": True})
    acc, res = _poll_output(reg, started["task_id"], until_status="exited")
    assert res["status"] == "exited"
    assert "ăn uống →" in acc


def test_background_task_runs_and_exits(executor):
    registry = _registry(executor)
    started = registry.execute("run_shell", {"command": QUICK_ECHO, "run_in_background": True})
    assert started["status"] == "running" and started["task_id"]
    output, last = _poll_output(registry, started["task_id"], until_status="exited")
    assert (last["status"], last["exit_code"]) == ("exited", 0) and "quick_done" in output
    assert registry.execute("shell_task_output", {"task_id": started["task_id"]})["output"] == ""  # each read brings only what is new


def test_the_background_start_names_the_task_id_in_the_models_view(executor):
    """The report: 6/6 background calls came back as `(no output) [exit unknown]`, so the
    model never learned the id and guessed (`shell_task_output(task_id="1")` → unknown
    task). The RESULT dict always carried the id; the render is what the model reads, and
    it was falling into the generic output/status path."""
    from harness.tools.contract import safe_render

    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    args = {"command": QUICK_ECHO, "run_in_background": True}
    started = reg.execute("run_shell", args)
    shown = safe_render(reg.contract("run_shell"), "run_shell", args, started)

    assert started["task_id"], "the executor must mint an id"
    assert started["task_id"] in shown, f"the model must see the id, got {shown!r}"
    assert "shell_task_output" in shown  # and what to do with it
    # The id the render names is the one the task tool answers to.
    polled = reg.execute("shell_task_output", {"task_id": started["task_id"]})
    assert polled["task_id"] == started["task_id"] and "error" not in polled


def test_background_task_kill(executor):
    registry = _registry(executor)
    started = registry.execute("run_shell", {"command": ECHO_THEN_SLEEP, "run_in_background": True})
    assert "started" in _poll_output(registry, started["task_id"])[0]  # running, and printing
    assert registry.execute("shell_task_kill", {"task_id": started["task_id"]})["status"] == "killed"
    assert registry.execute("shell_task_output", {"task_id": started["task_id"]})["status"] == "exited"


def test_background_unknown_task_errors(executor):
    registry = _registry(executor)
    for tool in ("shell_task_output", "shell_task_kill"):
        assert "unknown task" in registry.execute(tool, {"task_id": "bg-99"})["error"]


# -- a command that asks a question -------------------------------------------------
#
# Measured before this was written: the executor queued the exit-status line on the SAME
# stdin the command read, so a prompt swallowed it and the result came back with no output
# and an unknown status — which reads as "it worked". Every command runs
# with stdin CLOSED, so the prompt gets EOF instead: it cannot be answered, but it also
# cannot hold the turn.


def test_a_prompt_gets_eof_instead_of_holding_the_turn(tmp_path):
    """Both prompt shapes complete fast with an empty answer. On Windows `Read-Host` reads
    an empty string; on POSIX `read` fails and the next statement still runs. Either way
    the honest consequence of "nothing here can type" is an empty value, not a wedge."""
    ex = LocalExecutor(cwd=tmp_path, default_timeout=6)
    results = {}
    try:
        for name, command in (("echoes", PROMPT_ECHOES), ("silent", PROMPT_SILENT)):
            start = time.monotonic()
            result = ex.run(command, timeout=6)
            elapsed = time.monotonic() - start
            assert elapsed < 3.0, f"a prompt held the turn: {command!r}"
            assert result.get("status") != "background"
            assert result["exit_code"] == 0
            results[name] = result
    finally:
        ex.close()
    # The echoing shape proves it was the PROMPT that read (and got) the EOF.
    assert "after:" in results["echoes"]["output"]


def test_a_silent_timeout_is_backgrounded_and_names_the_input_reading(tmp_path):
    """A silent timeout has two readings — a slow command, or one waiting for input that
    nothing can type — and they are not distinguishable from here. The old hint hedged
    between them; now the command is not stopped at all, so the note says that first and
    keeps the input reading as the one thing the model may still have to fix."""
    ex = LocalExecutor(cwd=tmp_path, default_timeout=3)
    task_id = None
    try:
        result = ex.run(SLEEP_5, timeout=2)
        task_id = result["task_id"]
        assert result["status"] == "background"
        assert result["timed_out"] is True
        note = result["note"]
        assert "NOT stopped" in note
        assert "do not poll" in note
        assert "stdin closed" in note  # the input reading, kept
        assert "--non-interactive" in note  # and the fix for it
        assert ex.background_output(task_id)["status"] == "running"
    finally:
        if task_id:
            ex.background_kill(task_id)
        ex.close()


def test_a_timeout_after_output_keeps_it_and_the_command(tmp_path):
    """The report that started this: a command that had already stopped both servers
    printed its marker and then got a result whose text claimed nothing was printed. The
    output must survive, the command must NOT be killed, and what the model READS must
    carry both facts — the render is what it sees, not the JSON."""
    from harness.tools.contract import safe_render
    from harness.tools.shell import RUN_SHELL_CONTRACT

    ex = LocalExecutor(cwd=tmp_path, default_timeout=2)
    args = {"command": TIMEOUT_AFTER_PRINT}
    task_id = None
    try:
        result = ex.run(args["command"], timeout=2)
        task_id = result["task_id"]
        assert result["status"] == "background"
        assert "=== stop servers ===" in result["output"], "the printed line must be returned"
        assert isinstance(result.get("elapsed_seconds"), (int, float))

        # What the model reads carries both halves, not just the status bracket.
        shown = safe_render(RUN_SHELL_CONTRACT, "run_shell", args, result)
        assert "=== stop servers ===" in shown
        assert "NOT stopped" in shown
        assert "do not poll" in shown
        assert task_id in shown
        assert "timed out" not in shown  # the old wording claimed a stop
    finally:
        if task_id:
            ex.background_kill(task_id)
        ex.close()


def test_an_ordinary_command_is_untouched(tmp_path):
    """The check must be able to stay silent, or it is not a check."""
    ex = LocalExecutor(cwd=tmp_path, default_timeout=6)
    result = ex.run("echo hello", timeout=6)

    assert result["exit_code"] == 0
    assert result.get("error") is None
    assert "hello" in result["output"]


def test_the_shell_survives_a_swallowed_prompt(tmp_path):
    """The session must not be wedged by one interactive command."""
    ex = LocalExecutor(cwd=tmp_path, default_timeout=6)
    ex.run(PROMPT_ECHOES, timeout=6)
    after = ex.run("echo still_here", timeout=6)

    assert after["exit_code"] == 0
    assert "still_here" in after["output"]


def test_the_card_says_still_running_not_timed_out(tmp_path):
    """The row a person reads: "Command timed out" stopped being true the moment the
    command stopped being killed — it is still running, and the card has to say so."""
    from harness.tools.contract import safe_present_result
    from harness.tools.shell import RUN_SHELL_CONTRACT

    ex = LocalExecutor(cwd=tmp_path, default_timeout=2)
    args = {"command": TIMEOUT_AFTER_PRINT}
    task_id = None
    try:
        result = ex.run(args["command"], timeout=2)
        task_id = result["task_id"]
        shown = safe_present_result(RUN_SHELL_CONTRACT, "run_shell", args, result)
    finally:
        if task_id:
            ex.background_kill(task_id)
        ex.close()

    assert shown is not None
    assert shown.summary == "Command still running"
    assert "still running after" in shown.detail
    assert "timed out" not in shown.summary

# -- a finished background task reports itself (no polling needed) --------------------


def _executor(tmp_path):
    from harness.tools.shell import LocalExecutor

    return LocalExecutor(cwd=tmp_path)


def _wait_for(predicate, seconds=15.0):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_finished_background_task_reports_itself(tmp_path):
    """The follow-up that makes "do not poll" true: the session is woken with the result
    instead of the model asking `shell_task_output` every ten seconds."""
    ex = _executor(tmp_path)
    seen: list[dict] = []
    ex.set_background_listener(seen.append)
    try:
        started = ex.run_background('echo "all done"')
        assert started["status"] == "running"
        assert "will be told" in started["note"]
        assert _wait_for(lambda: bool(seen)), "the listener was never called"
        info = seen[0]
        assert info["task_id"] == started["task_id"]
        assert info["exit_code"] == 0
        assert "all done" in info["output"]
    finally:
        ex.close()


def test_a_killed_task_does_not_report_itself(tmp_path):
    """The model stopped it on purpose; a completion notice for work it just cancelled is
    noise, and noise on this channel costs a model turn."""
    ex = _executor(tmp_path)
    seen: list[dict] = []
    ex.set_background_listener(seen.append)
    try:
        started = ex.run_background('Start-Sleep -Seconds 30' if os.name == "nt" else "sleep 30")
        ex.background_kill(started["task_id"])
        assert not _wait_for(lambda: bool(seen), seconds=2.0), "a killed task notified"
    finally:
        ex.close()


def test_the_notification_does_not_consume_the_output_cursor(tmp_path):
    """`shell_task_output` still owns the incremental read: a notification that ate the
    buffer would make the model's next look at the task show nothing."""
    ex = _executor(tmp_path)
    seen: list[dict] = []
    ex.set_background_listener(seen.append)
    try:
        started = ex.run_background('echo "line one"')
        assert _wait_for(lambda: bool(seen))
        after = ex.background_output(started["task_id"])
        assert "line one" in after["output"], after
    finally:
        ex.close()


def test_a_broken_listener_cannot_break_the_task(tmp_path):
    ex = _executor(tmp_path)

    def boom(_info):
        raise RuntimeError("listener bug")

    ex.set_background_listener(boom)
    try:
        started = ex.run_background('echo "still fine"')
        assert _wait_for(lambda: ex.background_output(started["task_id"])["status"] == "exited")
    finally:
        ex.close()


def test_the_tool_descriptions_no_longer_teach_polling():
    """The prompt half of the fix. The old text said "sends NO completion notification, so
    poll it" — which is what made the model write `sleep`/`ps` loops in the first place."""
    import json

    from harness.tools.shell import shell_tools

    tools = {t.__name__: t for t in shell_tools(_executor("."))}
    run_desc = json.dumps(tools["run_shell"].__tool_schema__)
    out_desc = json.dumps(tools["shell_task_output"].__tool_schema__)
    assert "YOU WILL BE TOLD" in run_desc
    assert "NEVER poll" in run_desc
    assert "sends NO completion notification" not in run_desc
    assert "do NOT need this to learn when the task finishes" in out_desc

# -- the manager side: the wake actually reaches the session --------------------------


def _manager(tmp_path, monkeypatch):
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.interface.server.manager import SessionManager

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=_Provider())


def test_building_an_engine_wires_the_background_listener(tmp_path, monkeypatch):
    """Without this line the executor finishes jobs into the void and the prompt's promise
    ("you will be told") would be a lie."""
    mgr = _manager(tmp_path, monkeypatch)
    engine = mgr.get_engine("s-wire")
    assert engine is not None
    executor = getattr(engine, "executor", None)
    assert getattr(executor, "_bg_listener", None) is not None
    executor.close()


def test_a_finished_background_task_wakes_its_session(tmp_path, monkeypatch):
    import asyncio

    from harness.session.sessions import SessionRecord

    mgr = _manager(tmp_path, monkeypatch)
    mgr.session_store.save(
        SessionRecord(
            session_id="s-wake",
            workspace=str(mgr.default_workspace or "."),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "start the build"}],
        )
    )
    asyncio.run(
        mgr._deliver_shell_result(
            "s-wake",
            {"task_id": "bg-1", "command": "npm run build", "exit_code": 0, "output": "built in 3s"},
        )
    )
    record = mgr.session_store.load("s-wake")
    blob = "\n".join(str(m.get("content")) for m in record.messages)
    assert "background task bg-1 finished — exit 0" in blob
    assert "built in 3s" in blob
    assert "Nothing to poll" in blob


def test_a_deleted_session_is_not_woken(tmp_path, monkeypatch):
    import asyncio

    mgr = _manager(tmp_path, monkeypatch)
    asyncio.run(
        mgr._deliver_shell_result(
            "gone", {"task_id": "bg-2", "command": "x", "exit_code": 0, "output": ""}
        )
    )
    assert mgr.session_store.load("gone") is None  # no crash, no resurrection

# -- the fence, and the real end-to-end path ------------------------------------------


def test_a_task_started_before_an_undo_is_not_delivered(tmp_path, monkeypatch):
    """Same fencing subagent children get: the person hid that span, so its completion must
    not reopen the conversation over undone work."""
    import asyncio

    from harness.session.sessions import SessionRecord

    mgr = _manager(tmp_path, monkeypatch)
    mgr.session_store.save(
        SessionRecord(
            session_id="s-fence",
            workspace=str(mgr.default_workspace or "."),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "start the build"}],
        )
    )
    mgr._revert_generation["s-fence"] = 1  # an undo happened after the task started
    asyncio.run(
        mgr._deliver_shell_result(
            "s-fence",
            {"task_id": "bg-9", "command": "npm run build", "exit_code": 0,
             "output": "done", "generation": 0},
        )
    )
    blob = "\n".join(str(m.get("content")) for m in mgr.session_store.load("s-fence").messages)
    assert "bg-9" not in blob, "a stale completion was delivered"

    # ...and a task started AFTER the undo still arrives.
    asyncio.run(
        mgr._deliver_shell_result(
            "s-fence",
            {"task_id": "bg-10", "command": "npm run build", "exit_code": 0,
             "output": "done", "generation": 1},
        )
    )
    blob = "\n".join(str(m.get("content")) for m in mgr.session_store.load("s-fence").messages)
    assert "bg-10" in blob


def test_the_real_path_wakes_the_session_without_a_poll(tmp_path, monkeypatch):
    """End to end through the REAL wiring: the executor's watcher thread, the manager's
    loop, `deliver_to_session`, and the model's next turn. The unit tests above call the
    pieces; this one proves they are connected."""
    import asyncio
    import threading

    from harness.session.sessions import SessionRecord

    mgr = _manager(tmp_path, monkeypatch)
    mgr.session_store.save(
        SessionRecord(
            session_id="s-e2e",
            workspace=str(mgr.default_workspace or "."),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "run the long build"}],
        )
    )
    loop = asyncio.new_event_loop()
    mgr._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        engine = mgr.get_engine("s-e2e")
        assert engine is not None
        executor = engine.executor
        started = executor.run_background('echo "build finished ok"')
        assert _wait_for(
            lambda: "bg-" in "\n".join(
                str(m.get("content")) for m in mgr.session_store.load("s-e2e").messages
            ),
            seconds=20.0,
        ), "the wake never reached the session"
        blob = "\n".join(str(m.get("content")) for m in mgr.session_store.load("s-e2e").messages)
        assert started["task_id"] in blob
        assert "build finished ok" in blob
        executor.close()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def test_a_polling_model_still_gets_its_output(tmp_path):
    """The change is ADDITIVE: a model that polls anyway (older habits) keeps working —
    `shell_task_output` reads the same buffer the notification previewed."""
    ex = _executor(tmp_path)
    try:
        started = ex.run_background('echo "poll me"')
        # Wait on the PROCESS, not by polling `background_output`: reading is what moves
        # the cursor, and a wait loop that reads would consume the very output under test.
        task = ex._bg_tasks[started["task_id"]]
        assert _wait_for(lambda: task.proc.poll() is not None)
        out = ex.background_output(started["task_id"])
        assert out["exit_code"] == 0 and "poll me" in out["output"]
    finally:
        ex.close()

def test_the_woken_turn_sees_the_result_without_asking(tmp_path, monkeypatch):
    """The accuracy-relevant half: the notification is in the MODEL-VISIBLE context of the
    turn the wake opens, so an answer that depends on the job's outcome can be given
    without a single poll."""
    import asyncio
    import threading

    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.interface.server.manager import SessionManager
    from harness.session.sessions import SessionRecord

    class _Recording(ProviderClient):
        def __init__(self):
            self.calls: list[list[dict]] = []

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append([dict(m) for m in messages])
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    provider = _Recording()
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    mgr.session_store.save(
        SessionRecord(
            session_id="s-see",
            workspace=str(mgr.default_workspace or "."),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "run the long build"}],
        )
    )
    loop = asyncio.new_event_loop()
    mgr._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        engine = mgr.get_engine("s-see")
        executor = engine.executor
        executor.run_background('echo "TESTS PASSED 42"')
        assert _wait_for(lambda: bool(provider.calls), seconds=20.0), "no turn ran"
        blob = "\n".join(str(m.get("content")) for m in provider.calls[-1])
        assert "TESTS PASSED 42" in blob, "the result was not in the model's context"
        assert "finished — exit 0" in blob
        executor.close()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def test_a_millisecond_timeout_is_read_as_milliseconds():
    """Models with a `timeoutMs` habit pass ms; without normalisation `300000` meant ten
    minutes instead of five (PI's harness normalises the same way)."""
    from harness.tools.shell import _MAX_TIMEOUT, _clamp_timeout

    assert _clamp_timeout(300) == 300.0
    assert _clamp_timeout(300_000) == 300.0  # ms for five minutes
    assert _clamp_timeout(90_000) == 90.0
    assert _clamp_timeout(1_200_000) == _MAX_TIMEOUT  # ms for twenty minutes, capped
    assert _clamp_timeout(99_999_999) == _MAX_TIMEOUT
    assert _clamp_timeout(0.001) == 1.0  # never a zero wait


# -- the process leaving and its last lines arriving are two events ------------------------
#
# Real store, 2026-10-04: 93 of 2,446 run_shell results said "the command's shell exited
# before it reported a status", 87 of them on one busy day, and nearly all carried "(no
# output)" for commands that had printed. The wait asked the PROCESS whether it was done
# (every 50 ms) and returned on yes; the process leaves before its last lines are read. It
# now sleeps on the output stream and ends on the trailer or the end of the pipe.


class _LaggingReader:
    """A real, finished task whose lines reach the buffer a few reads late: what a busy
    machine does between a process exiting and its reader thread draining the pipe."""

    def __init__(self, task, lag):
        self._task = task
        self._lag = lag

    def drain_new(self):
        if self._lag > 0:
            self._lag -= 1
            return []
        return self._task.drain_new()

    @property
    def done(self):
        return True

    @property
    def output_ended(self):
        return self._lag <= 0 and self._task.output_ended

    def __getattr__(self, name):
        return getattr(self._task, name)


def _lagging(ex, monkeypatch, lag):
    real = ex._task_for

    def task_for(*args, **kwargs):
        task = real(*args, **kwargs)
        deadline = time.monotonic() + 20
        while not (task.done and task.output_ended) and time.monotonic() < deadline:
            time.sleep(0.02)
        return _LaggingReader(task, lag)

    monkeypatch.setattr(ex, "_task_for", task_for)


def test_a_command_that_exits_before_its_output_is_read_still_returns_it(executor, monkeypatch):
    _lagging(executor, monkeypatch, lag=3)
    result = executor.run("echo hello_from_the_pipe")
    assert "hello_from_the_pipe" in result["output"]
    assert result["exit_code"] == 0
    assert not result.get("error")


def test_what_only_the_trailer_carries_survives_a_late_read(executor, monkeypatch, tmp_path):
    """The working directory a command ends in is reported by the trailer line and by nothing
    else. A wait that stopped before the trailer arrived left the executor in the old one."""
    (tmp_path / "sub").mkdir()
    _lagging(executor, monkeypatch, lag=3)
    result = executor.run("cd sub")
    assert result["exit_code"] == 0 and not result.get("error")
    assert executor.cwd.endswith("sub")


def test_a_pipe_that_never_closes_does_not_hang_the_wait(executor, monkeypatch):
    """A child the command left running can hold the pipe open after the shell has gone.
    The wait gives up after `PIPE_HELD_SECONDS` and says what is true: the status is known
    (the process's own), and what that child prints later is not in the result."""
    from harness.tools import shell_wait

    class _HeldOpen:
        command = "left a child running"

        def __init__(self):
            self.proc = type("P", (), {"poll": lambda self: 0, "wait": lambda self, timeout=None: 0})()
            self._lines = ["partial\n"]

        def drain_new(self):
            lines, self._lines = self._lines, []
            return lines

        done = True
        output_ended = False

        def wait_output(self, timeout):
            time.sleep(min(timeout, 0.02))

        def wake(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(shell_wait, "PIPE_HELD_SECONDS", 0.3)
    monkeypatch.setattr(executor, "_task_for", lambda *a, **k: _HeldOpen())
    started = time.monotonic()
    result = executor.run("anything")
    assert 0.25 <= time.monotonic() - started < 3
    assert "partial" in result["output"]
    assert result["exit_code"] == 0 and result["ended"] == "held"
    assert "still holds its output open" in result["error"]


def test_a_command_that_ends_its_own_shell_is_a_plain_result(executor):
    """`exit 7` prints no trailer and loses nothing: the pipe closed, so every line was read,
    and the process's code is the status. It used to carry "the command's shell exited
    before it reported a status" beside `[exit 7]`; one real turn had that on 4 of 11
    commands, all of them correct (session 14715fae-61f, 2026-10-04)."""
    from harness.tools.shell import RUN_SHELL_CONTRACT

    result = executor.run("echo before_exit; exit 7")
    assert result["exit_code"] == 7 and "before_exit" in result["output"]
    assert result["ended"] == "ended" and not result.get("error")
    rendered = RUN_SHELL_CONTRACT.render({}, result)
    assert "[exit 7;" in rendered and rendered.count("[") == 1

    facts = RUN_SHELL_CONTRACT.facts({}, result)
    assert facts["exit_code"] == 7 and facts["ended"] == "ended"
    # A command that reports through its trailer says so, in the same field.
    assert RUN_SHELL_CONTRACT.facts({}, executor.run("echo fine"))["ended"] == "reported"


def test_a_shell_that_ends_with_no_status_says_so(executor, monkeypatch):
    """The one case the old sentence was true for: nothing reported a status at all."""

    class _Gone:
        command = "killed from outside"
        done = True
        output_ended = True

        def __init__(self):
            self.proc = type("P", (), {"poll": lambda self: None, "wait": lambda self, timeout=None: None})()

        def drain_new(self):
            return []

        def wait_output(self, timeout):
            pass

        def wake(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(executor, "_task_for", lambda *a, **k: _Gone())
    result = executor.run("anything")
    assert result["exit_code"] is None
    assert "ended without an exit status" in result["error"]


def test_a_command_returns_when_its_trailer_is_read_not_at_the_next_tick(executor):
    """The wait sleeps on the output stream, so a command is over the moment its trailer is in
    the buffer. Ten quick commands used to cost up to half a second of polling on top of
    their own time; what is pinned here is that the wait adds no fixed tick of its own."""
    executor.run("echo warm")  # the first spawn pays for the shell's own start-up
    started = time.monotonic()
    waits = []
    real = executor._task_for

    def task_for(*args, **kwargs):
        task = real(*args, **kwargs)
        wait_output = task.wait_output

        def counted(timeout):
            waits.append(timeout)
            wait_output(timeout)

        task.wait_output = counted
        return task

    executor._task_for = task_for
    result = executor.run("echo quick")
    assert "quick" in result["output"] and result["exit_code"] == 0
    # It waited ON THE STREAM (not on a clock), and was woken by output and the end of the
    # pipe: a handful of waits, never a train of ticks.
    assert 1 <= len(waits) <= 6, waits
    assert time.monotonic() - started < 5


def test_a_stop_wakes_the_wait_instead_of_waiting_for_the_next_line(tmp_path):
    import threading

    from harness.tools import shell_wait

    # With the wait's own look at the process pushed out of reach, only a wake can end it early.
    original = shell_wait.PROCESS_CHECK_SECONDS
    shell_wait.PROCESS_CHECK_SECONDS = 30.0
    ex = LocalExecutor(cwd=tmp_path, default_timeout=60)
    try:
        slow = "Start-Sleep -Seconds 30" if sys.platform == "win32" else "sleep 30"
        threading.Timer(0.7, ex.interrupt_now).start()
        started = time.monotonic()
        result = ex.run(slow)
        assert time.monotonic() - started < 10
        assert result["error"] == "interrupted by user"
    finally:
        shell_wait.PROCESS_CHECK_SECONDS = original
        ex.close()


# -- waiting for a background task in one call -------------------------------------


def _sleep_then_echo(seconds, text):
    if os.name == "nt":
        return f'Start-Sleep -Seconds {seconds}; Write-Output "{text}"'
    return f'sleep {seconds}; echo "{text}"'


def test_a_wait_returns_when_the_task_finishes(tmp_path):
    """Models polled a running task a step at a time (runs of 13, 9 and 5); one call that
    waits replaces the run, and it answers as soon as the task is done, not at the limit."""
    import time

    ex = _executor(tmp_path)
    try:
        started = ex.run_background(_sleep_then_echo(1, "finished now"))
        began = time.monotonic()
        out = ex.background_output(started["task_id"], wait=60)
        took = time.monotonic() - began
        assert out["status"] == "exited" and "finished now" in out["output"]
        assert took < 30
    finally:
        ex.close()


def test_a_wait_gives_up_at_its_limit_and_says_the_task_still_runs(tmp_path):
    import time

    ex = _executor(tmp_path)
    try:
        started = ex.run_background(_sleep_then_echo(30, "late"))
        began = time.monotonic()
        out = ex.background_output(started["task_id"], wait=1)
        took = time.monotonic() - began
        assert out["status"] == "running" and 0.9 <= took < 10
        ex.background_kill(started["task_id"])
    finally:
        ex.close()


def test_a_stop_ends_the_wait(tmp_path):
    import threading
    import time

    ex = _executor(tmp_path)
    try:
        started = ex.run_background(_sleep_then_echo(30, "late"))
        threading.Timer(0.5, ex._abort.set).start()
        began = time.monotonic()
        out = ex.background_output(started["task_id"], wait=60)
        assert out["status"] == "running" and time.monotonic() - began < 10
        ex.background_kill(started["task_id"])
    finally:
        ex.close()


def test_no_wait_and_a_bad_wait_answer_at_once(tmp_path):
    import time

    ex = _executor(tmp_path)
    try:
        started = ex.run_background(_sleep_then_echo(30, "late"))
        for wait in (0, "soon", -5, None):
            began = time.monotonic()
            assert ex.background_output(started["task_id"], wait=wait)["status"] == "running"
            assert time.monotonic() - began < 1
        ex.background_kill(started["task_id"])
    finally:
        ex.close()


def test_the_tool_offers_the_wait_with_its_limit():
    from harness.tools.shell import _TASK_OUTPUT_SCHEMA
    from harness.tools.shell_jobs import MAX_OUTPUT_WAIT_SECONDS

    wait = _TASK_OUTPUT_SCHEMA["function"]["parameters"]["properties"]["wait"]
    assert wait["maximum"] == MAX_OUTPUT_WAIT_SECONDS
    assert "pass `wait`" in _TASK_OUTPUT_SCHEMA["function"]["description"]


def test_a_wait_past_the_limit_is_held_to_it(tmp_path, monkeypatch):
    import time

    import harness.tools.shell_jobs as jobs

    monkeypatch.setattr(jobs, "MAX_OUTPUT_WAIT_SECONDS", 1)
    ex = _executor(tmp_path)
    try:
        started = ex.run_background(_sleep_then_echo(30, "late"))
        began = time.monotonic()
        assert ex.background_output(started["task_id"], wait=60)["status"] == "running"
        assert time.monotonic() - began < 10
        ex.background_kill(started["task_id"])
    finally:
        ex.close()


def test_the_tool_call_carries_the_wait_to_the_executor(tmp_path):
    from harness.tools.shell import shell_tools

    ex = _executor(tmp_path)
    try:
        tools = {t.__name__: t for t in shell_tools(ex)}
        started = ex.run_background(_sleep_then_echo(1, "done here"))
        out = tools["shell_task_output"](task_id=started["task_id"], wait=60)
        assert out["status"] == "exited" and "done here" in out["output"]
    finally:
        ex.close()
