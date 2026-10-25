"""A check's failure survives the shell reporting its last statement (2026-09-25 audit, W1).

`pytest | tail -5` exits with tail's status and `pyright *> log; Get-Content log` with
Get-Content's, so 27 of 103 checks in the audit's sessions read as passing while their
output said `N failed` — and every gate fed by the verification ledger (the red-check note,
the check-loop stop, goal proof) was blind to them. Two halves, both pinned here:

* the shell reports the first failing stage / native program, with a note saying so;
* the ledger reads a runner's own failing summary as a failure, whatever the exit code.

And a third, for a line of several statements (`shell_sequence`): `ruff check .; pytest`
with a red ruff and a green pytest reports ruff's exit and names it — read by the shell
itself (PowerShell's parser, bash's ERR trap), not guessed from the runner's wording.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from harness.tools.shell import LocalExecutor, _shell_render
from harness.tools.shell_status import _parse_cwd, _parse_details, _parse_exit_code, _pipeline_exit, _reported_exit
from harness.tools.shell_kinds import local_shells
from harness.tools.shell_sequence import sequence_failure, sequence_script
from harness.loop.verification import last_verification, parse_status

_WIN = sys.platform == "win32"
_BASH_SHELL = local_shells().get("bash")  # never the WSL launcher on PATH
_BASH = _BASH_SHELL.path if _BASH_SHELL else None


def _through_bash(tmp_path, command: str) -> tuple:
    """Run `command` plus the POSIX trailer in a real bash, and read it the way `run` does."""
    executor = LocalExecutor(cwd=tmp_path)
    script = sequence_script(command, powershell=False) + "\n" + executor._trailer(_BASH_SHELL)
    out = subprocess.run(
        [_BASH, "-c", script], capture_output=True, text=True, cwd=tmp_path, timeout=30
    ).stdout
    line = next(row for row in out.splitlines() if executor._marker in row)
    code, note = _reported_exit(
        _parse_exit_code(line, executor._marker),
        _parse_details(line, executor._marker),
        command,
    )
    return code, note, _parse_cwd(line, executor._marker)


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_a_failing_first_stage_is_the_pipelines_exit(tmp_path):
    code, note, cwd = _through_bash(tmp_path, "false | cat")
    assert code == 1
    assert note == "stage 1 of 2 exited 1; the pipeline's last stage exited 0"
    assert cwd  # the detail token does not swallow the working directory


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_a_stage_stopped_by_sigpipe_is_not_a_failure(tmp_path):
    # `head` stops reading and `yes` dies of SIGPIPE (141): nothing went wrong.
    assert _through_bash(tmp_path, "yes | head -1 >/dev/null")[:2] == (0, None)


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_a_compound_command_keeps_its_own_status(tmp_path):
    # `if false` leaves PIPESTATUS=1 while `$?` is 0 — only a real pipeline is read.
    assert _through_bash(tmp_path, "if false; then :; fi")[:2] == (0, None)
    assert _through_bash(tmp_path, "true | false")[:2] == (1, None)


@pytest.mark.skipif(not _WIN, reason="PowerShell's native exit code")
def test_a_native_failure_inside_a_succeeding_statement_is_reported(tmp_path):
    # One statement (a script block), which succeeds on its own after the program failed.
    result = LocalExecutor(cwd=tmp_path).run("& { cmd /c exit 3 ; Write-Output after }")
    assert result["exit_code"] == 3
    assert "though its last statement succeeded" in result["exit_note"]
    rendered = _shell_render({}, result)
    assert "exit 3 (a program in this command exited 3" in rendered


@pytest.mark.skipif(not _WIN, reason="Windows PowerShell 5.1's NativeCommandError")
def test_a_program_that_only_wrote_to_stderr_succeeded(tmp_path):
    # Session b3a8491e-9db (2026-09-28): `uv sync 2>&1 | Select-Object -Last 5` succeeded
    # and read `[exit 1]`, because 5.1 turns redirected stderr into error records and
    # sets `$?` False. The model learned "exit 1 is noise" and then misread a real one.
    executor = LocalExecutor(cwd=tmp_path)
    quiet = executor.run('cmd /c "echo warn 1>&2 & echo out" 2>&1 | Select-Object -Last 2')
    assert quiet["exit_code"] == 0, quiet
    # The same stderr with a real failure keeps the program's code...
    failed = executor.run('cmd /c "echo real 1>&2 & exit 3" 2>&1 | Select-Object -Last 1')
    assert failed["exit_code"] == 3, failed
    # ...and a cmdlet's own failure is still a failure.
    missing = executor.run("Get-Item C:\\definitely\\missing\\path -ErrorAction Continue")
    assert missing["exit_code"] != 0, missing


@pytest.mark.skipif(not _WIN, reason="PowerShell keeps only the last native exit code")
def test_a_failed_check_earlier_in_a_line_is_the_lines_exit(tmp_path):
    """b3a8491e-9db (2026-09-28): `ruff check …; pytest …` — ruff failed, pytest passed, the
    line read exit 0 and the ledger booked a pass. PowerShell's own parser splits the line
    now, and the first failing program is the line's exit, named."""
    executor = LocalExecutor(cwd=tmp_path)
    result = executor.run(
        "cmd /c \"echo Found 3 errors. & exit 1\"; cmd /c \"echo 5 passed in 0.10s\""
    )
    assert result["exit_code"] == 1, result
    assert result["exit_note"] == (
        '`cmd /c "echo Found 3 errors. & exit 1"` exited 1 earlier in this line; its last '
        "statement succeeded"
    )
    assert parse_status(_shell_render({}, result)) == (False, False)
    # Whatever the runner prints: no summary a text rule could have recognised.
    silent = executor.run('cmd /c exit 4; cmd /c "echo ok"')
    assert silent["exit_code"] == 4, silent
    assert parse_status(_shell_render({}, silent))[0] is False
    clean = executor.run("cmd /c \"echo 5 passed in 0.10s\"")
    assert "exit_note" not in clean


@pytest.mark.skipif(not _WIN, reason="PowerShell statement probes")
def test_a_line_keeps_behaving_as_a_line(tmp_path):
    """The wrapped line is still the person's command: scope, output, cwd, quoting."""
    (tmp_path / "sub").mkdir()
    executor = LocalExecutor(cwd=tmp_path)
    kept = executor.run("$x = 41; $x + 1")
    assert (kept["exit_code"], kept["output"].strip()) == (0, "42")
    multi = executor.run("Write-Output one\nWrite-Output two # a comment\n'three'")
    assert multi["output"].split() == ["one", "two", "three"]
    quoted = executor.run("Write-Output 'it''s'; Write-Output ‘smart’; 'Việt'")
    assert quoted["output"].split() == ["it's", "smart", "Việt"], quoted
    moved = executor.run("cd sub; Write-Output here")
    assert moved["exit_code"] == 0 and executor.cwd.endswith("sub"), executor.cwd
    # A failure inside a function is the program's, and the call is the statement named.
    called = executor.run("function f { cmd /c exit 5 }; f; 'after'")
    assert called["exit_code"] == 5 and "`f` exited 5" in called["exit_note"], called


@pytest.mark.skipif(not _WIN, reason="PowerShell statement probes")
def test_a_status_the_line_reads_itself_is_left_to_it(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    read = executor.run("cmd /c exit 1; if (-not $?) { 'handled' }")
    assert read["output"].strip() == "handled", read  # `$?` still the program's
    # ...and a failure the line looked at is not recorded as an earlier failure; the
    # leftover `$LASTEXITCODE` rule reports it as it always did.
    looked = executor.run("cmd /c exit 2; $code = $LASTEXITCODE; cmd /c exit 0; 'x'")
    assert looked["exit_code"] == 0 and "exit_note" not in looked, looked


@pytest.mark.skipif(not _WIN, reason="PowerShell parse errors")
def test_a_line_that_does_not_parse_is_still_explained(tmp_path):
    result = LocalExecutor(cwd=tmp_path).run("cd x; python a.py && echo y")
    assert "no `&&`" in (result.get("error") or ""), result
    assert "not a valid statement separator" in result["output"]
    assert "line 1, column" in result["output"]


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_bash_names_an_earlier_unhandled_failure(tmp_path):
    code, note, cwd = _through_bash(tmp_path, "false; echo after")
    assert (code, note) == (
        1,
        "an earlier command in this line exited 1; the line's last command succeeded",
    )
    assert cwd
    # Handled failures never fire bash's ERR trap.
    assert _through_bash(tmp_path, "false || true; echo after")[:2] == (0, None)
    assert _through_bash(tmp_path, "if false; then :; fi; echo after")[:2] == (0, None)
    # A line that reads the status itself decides for itself.
    assert _through_bash(tmp_path, "false; [ $? -ne 0 ] && echo handled")[:2] == (0, None)
    # The last statement's own failure keeps its own status, pipeline rule included.
    assert _through_bash(tmp_path, "true; false | cat")[:2] == (
        1,
        "stage 1 of 2 exited 1; the pipeline's last stage exited 0",
    )


def test_only_a_line_of_statements_is_wrapped():
    assert sequence_script("pytest -q", powershell=True) == "pytest -q"
    assert sequence_script("pytest -q", powershell=False) == "pytest -q"
    assert "ParseInput" in sequence_script("ruff check .; pytest", powershell=True)
    assert sequence_script("ruff check .; pytest", powershell=False).startswith("__cw_err=0; trap")
    long_line = "Write-Output x; " * 2000
    assert sequence_script(long_line, powershell=True) == long_line


def test_the_sequence_rule_on_its_own_terms():
    command = "ruff check .; pytest -q"
    assert sequence_failure("1@0-12", command) == (
        1,
        "`ruff check .` exited 1 earlier in this line; its last statement succeeded",
    )
    assert sequence_failure("0", command) is None
    assert sequence_failure("x", command) is None
    # The earlier failure wins over a clean last statement, never over a failed one.
    assert _reported_exit(0, {"err": "1@0-12", "native": "0"}, command)[0] == 1
    assert _reported_exit(2, {"err": "1@0-12", "native": "2"}, command) == (2, None)
    assert _reported_exit(0, {"native": "3"}, command)[0] == 3
    assert _reported_exit(0, {"ps": "2,0"}, "a | b")[0] == 2
    assert _reported_exit(None, {"err": "1"}, command) == (None, None)


@pytest.mark.skipif(not _WIN, reason="PowerShell keeps only the last native exit code")
def test_what_the_shell_cannot_see_is_still_read_from_the_summary(tmp_path):
    """A failure the line looked at and let go is not the shell's to report; the runner's
    own summary is still read, and the ledger books the check as red."""
    executor = LocalExecutor(cwd=tmp_path)
    result = executor.run(
        "cmd /c \"echo Found 3 errors. & exit 1\"; $null = $LASTEXITCODE; "
        "cmd /c \"echo 5 passed in 0.10s\""
    )
    assert result["exit_code"] == 0
    assert "but the output reports a failure: 'Found 3 errors.'" in result["exit_note"]
    assert parse_status(_shell_render({}, result)) == (False, False)


def test_the_rule_on_its_own_terms():
    assert _pipeline_exit(0, "ps=2,0,0") == (
        2,
        "stage 1 of 3 exited 2; the pipeline's last stage exited 0",
    )
    assert _pipeline_exit(0, "ps=141,141,0") == (0, None)
    assert _pipeline_exit(141, "ps=0,141") == (141, None)  # the last stage's 141 counts
    assert _pipeline_exit(0, "native=0") == (0, None)
    assert _pipeline_exit(2, "native=5") == (2, None)  # a failed last statement wins
    assert _pipeline_exit(None, "ps=1,0") == (None, None)
    assert _pipeline_exit(4, "") == (4, None)  # an old-style marker line


@pytest.mark.parametrize(
    "output",
    [
        "....F\n1 failed, 4 passed in 0.31s",
        "==== 2 errors in 1.02s ====",
        "Tests:       1 failed, 3 passed, 4 total",
        " Test Files  1 failed | 2 passed (3)",
        "src/a.ts(3,1): error TS2322: Type 'string' is not assignable",
        "Found 8 errors.",
        "180 errors, 0 warnings, 0 informations",
        "✖ 4 problems (2 errors, 2 warnings)",
    ],
)
def test_a_runner_summary_that_failed_is_a_failed_check(output):
    assert parse_status(f"{output}\n[exit 0; cwd /w]") == (False, False)


@pytest.mark.parametrize(
    "output",
    [
        "5 passed in 0.12s",
        "0 errors, 0 warnings, 0 informations",
        "All checks passed!",
        "the log says: 1 failed login for bob",
    ],
)
def test_a_passing_or_unrelated_output_stays_a_pass(output):
    assert parse_status(f"{output}\n[exit 0; cwd /w]") == (True, False)


def test_the_ledger_sees_the_masked_suite_as_red():
    messages = [
        {"role": "user", "content": "fix it"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "run_shell",
                        "arguments": '{"command": "pytest -q | tail -3"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "FAILED tests/test_a.py::test_x\n1 failed, 9 passed in 0.40s\n[exit 0; cwd /w]",
        },
    ]
    check = last_verification(messages)
    assert check is not None and check.ok is False
