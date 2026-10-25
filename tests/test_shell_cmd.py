"""`cmd.exe` as a `run_shell` dialect (`harness/tools/shell_cmd.py`).

cmd has no later line to ask for the previous command's status, so the trailer rides on the same
line. What has to hold, measured on a real cmd.exe:

  - the exit status and the working directory are the command's own, and the directory survives a
    change of shell;
  - a `for` or `if` the command ends with is not given the trailer as its body (a marker per item);
  - a program that ended without a newline does not swallow the marker line;
  - output is UTF-8 (a Vietnamese name in `dir`/`echo`), which needs a second cmd started AFTER
    `chcp 65001`;
  - several lines are a batch file, which is deleted when the process is done with it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from harness.tools import shell_cmd
from harness.tools.shell import LocalExecutor
from harness.tools.shell_kinds import BASH, CMD, POWERSHELL, Shell, find_shells
from harness.tools.shell_status import _parse_error_hint, _unknown_tool_hint

_REAL = find_shells()
_WIN_CMD = sys.platform == "win32" and CMD in _REAL
CMDSHELL = Shell(CMD, r"C:\Windows\System32\cmd.exe", "cmd")
GITBASH = Shell(BASH, r"C:\Program Files\Git\bin\bash.exe", "Git Bash")

# -- composing the line -------------------------------------------------------------------------


def test_a_one_line_command_is_wrapped_and_followed_by_a_trailer_that_defers_its_expansion():
    script, temps = shell_cmd.compose("echo hi", "MARK")
    assert temps == []
    assert script == "(echo hi)& echo.& call echo MARK %^ERRORLEVEL% %^CD%"
    # `%^ERRORLEVEL%` is the whole point: a plain `%ERRORLEVEL%` is read before the command runs.
    assert "%ERRORLEVEL%" not in script


def test_a_background_cmd_has_no_trailer():
    script, temps = shell_cmd.compose("echo hi", None)
    assert script == "(echo hi)" and temps == []


def test_several_lines_become_a_batch_file_that_is_called():
    script, temps = shell_cmd.compose("set X=5\necho %X%\nexit /b 3", "MARK")
    try:
        assert len(temps) == 1 and temps[0].endswith(".cmd")
        assert script.startswith(f'(call "{temps[0]}")')
        body = Path(temps[0]).read_bytes()
        assert body.startswith(b"@echo off\r\n") and b"\r\nexit /b 3\r\n" in body  # CRLF, as a batch file wants
    finally:
        for path in temps:
            os.unlink(path)


def test_the_command_line_runs_the_script_in_a_cmd_started_after_chcp():
    line = shell_cmd.command_line(CMDSHELL.path, "(echo hi)")
    assert line == (
        '"C:\\Windows\\System32\\cmd.exe" /d /s /c "chcp 65001>nul & '
        '"C:\\Windows\\System32\\cmd.exe" /d /s /c "(echo hi)""'
    )


def test_spawn_hands_a_string_to_the_system_exactly_as_built():
    """A list would be quoted with a backslash before each inner quote, which cmd does not read."""
    from harness.platform import spawn as spawn_module

    seen = {}

    class Fake:
        def __init__(self, args, **kwargs):
            seen["args"] = args

    original = spawn_module.subprocess.Popen
    spawn_module.subprocess.Popen = Fake
    try:
        spawn_module.spawn('cmd /c "echo \\"x\\""')
        spawn_module.spawn(["a", "b"])
    finally:
        spawn_module.subprocess.Popen = original
    assert seen["args"] == ["a", "b"]  # the second call; the first is checked below
    spawn_module.subprocess.Popen = Fake
    try:
        spawn_module.spawn("one string")
    finally:
        spawn_module.subprocess.Popen = original
    assert seen["args"] == "one string"


# -- on a real cmd.exe --------------------------------------------------------------------------


def _executor(tmp_path):
    return LocalExecutor(cwd=tmp_path)


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_the_exit_status_and_the_directory_are_the_commands_own(tmp_path):
    (tmp_path / "sub dir").mkdir()
    executor = _executor(tmp_path)
    assert executor.run("echo hi", shell="cmd")["output"].strip() == "hi"
    assert executor.run('python -c "import sys; sys.exit(7)"', shell="cmd")["exit_code"] == 7
    assert executor.run("frobnicate_zz", shell="cmd")["exit_code"] == 9009
    executor.run('cd "sub dir"', shell="cmd")
    assert executor.cwd.endswith("sub dir")
    assert executor.run("cd", shell="cmd")["output"].strip().endswith("sub dir")


@pytest.mark.skipif(not (_WIN_CMD and POWERSHELL in _REAL), reason="needs cmd.exe and PowerShell")
def test_the_directory_cmd_left_is_the_next_shells_starting_point(tmp_path):
    (tmp_path / "sub dir").mkdir()
    executor = _executor(tmp_path)
    executor.run('cd "sub dir"', shell="cmd")
    seen = executor.run("(Get-Location).Path", shell="powershell")["output"].strip()
    assert seen.endswith("sub dir")


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_a_loop_the_command_ends_with_is_not_given_the_trailer(tmp_path):
    # Each iteration waits a second BEFORE it prints, so a trailer that became the loop body would
    # report its first marker right after "a" and the wait would end a second before "b".
    result = _executor(tmp_path).run("for %i in (a b) do @ping -n 2 127.0.0.1 >nul& echo %i", shell="cmd")
    assert result["output"].split() == ["a", "b"] and result["exit_code"] == 0


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_output_that_ends_without_a_newline_is_kept_and_the_status_is_still_read(tmp_path):
    result = _executor(tmp_path).run("<nul set /p=partial", shell="cmd")
    assert "partial" in result["output"] and result["exit_code"] == 1  # `set /p` with no input is 1


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_a_vietnamese_name_survives_cmds_own_output(tmp_path):
    (tmp_path / "Nguyễn Văn Ánh đạt.txt").write_text("x", encoding="utf-8")
    executor = _executor(tmp_path)
    assert executor.run("echo Nguyễn Văn Ánh đạt", shell="cmd")["output"].strip() == "Nguyễn Văn Ánh đạt"
    assert "Nguyễn Văn Ánh đạt.txt" in executor.run("dir /b", shell="cmd")["output"]


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_a_multi_line_command_is_a_batch_and_its_file_is_deleted_afterwards(tmp_path):
    before = {p for p in os.listdir(tempfile.gettempdir()) if p.startswith("reeve-")}
    result = _executor(tmp_path).run("set X=5\nfor %%i in (1 2) do echo n%%i\necho x is %X%\nexit /b 3", shell="cmd")
    assert result["exit_code"] == 3
    assert result["output"].split() == ["n1", "n2", "x", "is", "5"]
    deadline = time.time() + 5
    while time.time() < deadline:
        left = {p for p in os.listdir(tempfile.gettempdir()) if p.startswith("reeve-")} - before
        if not left:
            break
        time.sleep(0.1)
    assert not left, left


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_cmd_runs_in_the_background_and_can_be_stopped(tmp_path):
    executor = _executor(tmp_path)
    started = executor.run_background("echo from-bg & ping -n 3 127.0.0.1 >nul", shell="cmd")
    assert started["status"] == "running"
    slow = executor.run("ping -n 30 127.0.0.1 >nul", timeout=1, shell="cmd")
    assert slow["status"] == "background"
    assert executor.background_kill(slow["task_id"])["status"] == "killed"
    deadline = time.time() + 10
    out = ""
    while time.time() < deadline and "from-bg" not in out:
        out += executor.background_output(started["task_id"])["output"]
        time.sleep(0.2)
    assert "from-bg" in out


@pytest.mark.skipif(not _WIN_CMD, reason="needs cmd.exe")
def test_cmd_is_a_choice_the_executor_offers_and_never_the_default(tmp_path):
    executor = _executor(tmp_path)
    assert CMD in [s.kind for s in executor.shells()]
    assert executor._shell_for(None).kind == POWERSHELL


# -- a refusal in cmd ----------------------------------------------------------------------------


def test_a_line_cmd_cannot_parse_says_so_and_how_a_multi_line_command_runs():
    hint = _parse_error_hint(") was unexpected at this time.", CMDSHELL, BASH)
    assert "cmd could not parse" in hint and "batch file" in hint and 'shell="bash"' in hint
    assert _parse_error_hint("all fine", CMDSHELL, BASH) is None


def test_a_posix_tool_in_cmd_is_named_when_bash_is_there():
    output = "'grep' is not recognized as an internal or external command,\noperable program or batch file."
    hint = _unknown_tool_hint(output, CMDSHELL, BASH)
    assert 'shell="bash"' in hint and "ran in cmd" in hint
    assert _unknown_tool_hint(output, CMDSHELL, None) is None
    assert _unknown_tool_hint(output, GITBASH, BASH) is None
