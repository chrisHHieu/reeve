"""A program ended by its own pipeline has no exit status of its own.

Windows PowerShell 5.1 ends a native program whose output goes into `Select-Object -First N` as soon
as N objects arrived, and the program's `$LASTEXITCODE` becomes -1 whatever it would have exited with
(measured: a program that would have exited 3 reads -1; one that finished before the cut keeps its own
code; PowerShell 7 waits for the program and keeps the real one). The harness reported that -1 as the
line's status, so the GUI drew a red `exit -1` on commands that had worked: 36 of 2,738 real
`run_shell` results, all piped into `Select-Object -First`, 18% of all commands using it as `| head`.
bash has the same shape (a stage killed by SIGPIPE when a later one stops reading) and already ignored it.

What has to hold, as one rule across both dialects:

  - a listing, a log tail, any command that is not a check, reports what its last stage says: 0;
  - a CHECK ended that way is `unknown`, neither passed nor failed: the suite did not finish, and
    booking it as passed or (as before) as failed is a claim nobody measured;
  - the program's own status is believed whenever it was real: it finished before the cut, the consumer
    does not stop the pipeline (`-Last`, `-Skip`, `-Property`), an earlier statement failed on its own;
  - the cut is found from the syntax tree, whatever the spelling (`select -first`, `-Fi`, `-First:1`,
    `-Index`), and a string that merely mentions the words is not one.
"""

from __future__ import annotations

import sys

import pytest

from harness.tools.shell import LocalExecutor, _shell_status
from harness.tools.shell_kinds import BASH, POWERSHELL, Shell, local_shells
from harness.tools.shell_sequence import sequence_script
from harness.tools.shell_status import _reported_exit

PS51 = Shell(POWERSHELL, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "PowerShell 5.1")
PS7 = Shell("pwsh", r"C:\Program Files\PowerShell\7\pwsh.exe", "PowerShell 7")

_REAL = local_shells()
_PS51 = POWERSHELL in _REAL and sys.platform == "win32"
_BASH = BASH in _REAL

# -- the rule -------------------------------------------------------------------------------------


def test_a_cut_program_in_a_listing_reports_what_the_last_stage_says():
    # PowerShell 5.1 hands the whole line -1 when `$?` is false; the cut is what said so.
    assert _reported_exit(-1, {"native": "-1", "cut": "1"}, "git log | Select-Object -First 5") == (0, None)
    assert _reported_exit(0, {"native": "-1", "cut": "1"}, "git log | Select-Object -First 5") == (0, None)
    # bash: a stage that died of SIGPIPE because `head` stopped reading.
    assert _reported_exit(0, {"ps": "141,0"}, "yes | head -1") == (0, None)


def test_a_check_that_was_cut_is_unknown_not_passed_and_not_failed():
    for details, code in (({"native": "-1", "cut": "1"}, -1), ({"ps": "141,0"}, 0)):
        status, note = _reported_exit(code, details, "python -m pytest -q | Select-Object -First 20")
        assert status is None
        assert "ended by the pipe" in note and "result is unknown" in note and "Select-Object -First" in note


def test_the_programs_own_status_is_still_believed_when_it_was_real():
    assert _reported_exit(0, {"ps": "1,0"}, "pytest | tail") [0] == 1  # a stage that failed on its own
    assert _reported_exit(3, {"native": "3"}, "x | Select-Object -First 3")[0] == 3  # finished before the cut
    assert _reported_exit(0, {"native": "0"}, "x | Select-Object -Last 3") == (0, None)


def test_an_earlier_failure_in_the_line_wins_over_a_cut_last_statement():
    status, note = _reported_exit(-1, {"native": "-1", "cut": "1", "err": "5@0-9"}, "python a.py; x | select -first 1")
    assert status == 5 and "exited 5 earlier in this line" in note


def test_an_unknown_status_still_says_why():
    line = _shell_status({"exit_code": None, "exit_note": "this check was ended by the pipe", "cwd": "."})
    assert line.startswith("[exit unknown (this check was ended by the pipe)")
    assert _shell_status({"exit_code": None, "cwd": "."}).startswith("[exit unknown;")


# -- finding the cut ---------------------------------------------------------------------------------


def test_only_a_line_that_could_pipe_into_select_is_parsed():
    assert "ParseInput" in sequence_script("x | Select-Object -First 1", powershell=True)
    assert "ParseInput" in sequence_script("x | select -first 1", powershell=True)
    for plain in ("x | Out-Null", "Write-Output select", "x", "Get-Content f | Where-Object { $_ }"):
        assert sequence_script(plain, powershell=True) == plain
    # bash is untouched: it has no such status, its PIPESTATUS already says what ended.
    assert sequence_script("x | head -1", powershell=False) == "x | head -1"


def test_only_windows_powershell_51_reports_a_cut():
    """7 waits for the program and keeps its real exit code, so a -1 there is a real -1."""
    executor = LocalExecutor(cwd=".", shells={POWERSHELL: PS51, "pwsh": PS7})
    assert "cut=1" in executor._trailer(PS51)
    assert "cut=1" not in executor._trailer(PS7)


# -- on a real Windows PowerShell 5.1 -----------------------------------------------------------------

_PY = sys.executable
#: Prints a line, then keeps running for a second and exits 3: it is still running when the pipe cuts it.
#: A script file, so the command has no `;` of its own: a single pipeline is what has to be parsed.
_SLOW_SCRIPT = "\n".join(
    [
        "import time, sys",
        "print(sys.argv[1] if len(sys.argv) > 1 else 'x', flush=True)",
        "time.sleep(1)",
        "sys.exit(3)",
        "",
    ]
)


@pytest.fixture()
def slow(tmp_path):
    (tmp_path / "slow.py").write_text(_SLOW_SCRIPT, encoding="utf-8")
    return f"& \"{_PY}\" slow.py 2>&1"


def _run(tmp_path, command):
    return LocalExecutor(cwd=tmp_path).run(command, shell=POWERSHELL)


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
@pytest.mark.parametrize(
    "consumer",
    [
        "Select-Object -First 1",
        "select -first 1",
        "Select-Object -Fi 1",
        "Select-Object -First:1",
        "Select-Object -Index 0",
    ],
)
def test_every_spelling_of_a_cut_is_found_and_the_line_is_not_a_failure(tmp_path, slow, consumer):
    result = _run(tmp_path, f"{slow} | {consumer}")
    assert result["exit_code"] == 0 and result["output"].strip() == "x"
    assert "exit_note" not in result


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
@pytest.mark.parametrize("consumer", ["Select-Object -Skip 0", "Select-Object -Property Length", "Select-Object -Last 3"])
def test_a_consumer_that_does_not_stop_the_pipeline_leaves_the_programs_real_status(tmp_path, slow, consumer):
    assert _run(tmp_path, f"{slow} | {consumer}")["exit_code"] == 3


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
def test_a_program_that_finished_before_the_cut_keeps_its_own_status(tmp_path):
    command = f"& \"{_PY}\" -c \"import sys; print(1); sys.exit(3)\" 2>&1 | Select-Object -First 3"
    assert _run(tmp_path, command)["exit_code"] == 3


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
def test_a_cut_in_the_middle_of_a_line_is_not_an_earlier_failure(tmp_path, slow):
    result = _run(tmp_path, f"{slow} | Select-Object -First 1; Write-Output done")
    assert result["exit_code"] == 0 and "done" in result["output"] and "exit_note" not in result


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
def test_a_real_failure_before_a_cut_still_fails_the_line(tmp_path, slow):
    command = f"& \"{_PY}\" -c \"import sys; sys.exit(5)\"; {slow} | Select-Object -First 1"
    result = _run(tmp_path, command)
    assert result["exit_code"] == 5 and "earlier in this line" in result["exit_note"]


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
def test_words_in_a_string_are_not_a_cut(tmp_path):
    result = _run(tmp_path, 'Write-Output "x | select -first 1"')
    assert result["exit_code"] == 0 and "select -first 1" in result["output"]


@pytest.mark.skipif(not _PS51, reason="needs Windows PowerShell 5.1")
def test_a_check_ended_by_the_pipe_is_unknown_and_the_ledger_agrees(tmp_path, slow):
    from harness.loop.verification import parse_status

    command = f"{slow.replace('slow.py', 'slow.py pytest')} | Select-Object -First 1"
    result = _run(tmp_path, command)
    assert result["exit_code"] is None and "result is unknown" in result["exit_note"]
    from harness.tools.shell import _shell_render

    rendered = _shell_render({}, result)
    assert parse_status(rendered) == (None, False)  # neither a pass nor a red check for the gates


# -- on a real bash -------------------------------------------------------------------------------------


@pytest.mark.skipif(not _BASH, reason="needs bash")
def test_a_check_cut_by_head_is_unknown_in_bash_too_and_a_listing_is_not(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    listing = executor.run("yes | head -1", shell=BASH)
    assert listing["exit_code"] == 0 and "exit_note" not in listing
    check = executor.run("yes pytest | head -1", shell=BASH)
    assert check["exit_code"] is None and "ended by the pipe" in check["exit_note"]
    failing = executor.run("(echo pytest; exit 2) | head -1", shell=BASH)
    assert failing["exit_code"] == 2  # it finished and failed on its own: believed
