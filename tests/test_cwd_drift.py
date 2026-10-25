"""A failed command that ran away from the session folder says so, naming the cd that moved it
(2026-10-15: six failures in one session came from a cd made earlier, each read as a broken tool)."""

from __future__ import annotations

import pytest

from harness.tools import shell_kinds
from harness.tools.shell import LocalExecutor
from harness.tools.shell.cwd_drift import drift_note
from harness.tools.shell_present import RUN_SHELL_CONTRACT

FAILED = {"exit_code": 127, "output": "bash: .venv/Scripts/python: No such file or directory\n"}


def test_a_failure_that_reads_like_a_wrong_path_names_the_earlier_cd(tmp_path):
    root, sub = str(tmp_path), str(tmp_path / "gui")
    note = drift_note(FAILED, cwd=sub, root=root, moved_by="cd gui && npm test")
    assert note is not None
    assert sub in note and root in note and "`cd gui && npm test`" in note


@pytest.mark.parametrize(
    "result",
    [
        {"exit_code": 0, "output": "No such file or directory"},  # it worked
        {"exit_code": 1, "output": "3 failed, 10 passed"},  # failed, but not about a path
        {"status": "background", "exit_code": None, "output": "ENOENT"},  # still running
    ],
)
def test_no_note_when_the_failure_is_not_about_where_it_ran(tmp_path, result):
    assert drift_note(result, cwd=str(tmp_path / "gui"), root=str(tmp_path), moved_by="cd gui") is None


def test_no_note_in_the_session_folder(tmp_path):
    assert drift_note(FAILED, cwd=str(tmp_path), root=str(tmp_path), moved_by=None) is None


@pytest.mark.skipif(not shell_kinds.find_shells(), reason="needs a real shell")
def test_a_real_shell_is_told_after_an_earlier_cd_and_not_by_the_cd_itself(tmp_path):
    (tmp_path / "gui").mkdir()
    executor = LocalExecutor(cwd=tmp_path)
    moved = executor.run("cd gui")
    assert "cwd_drift" not in moved
    again = executor.run("cd gui")  # already there: gui/gui does not exist
    assert again["exit_code"] not in (None, 0)
    assert again.get("cwd_drift") is True and "`cd gui`" in again["error"]
    assert RUN_SHELL_CONTRACT.facts({"command": "cd gui"}, again)["cwd_drift"] is True
    # The model reads it under the output.
    assert "a cd lasts across commands" in RUN_SHELL_CONTRACT.render({"command": "cd gui"}, again)
    back = executor.run(f'cd "{tmp_path}"')
    assert back["exit_code"] == 0
    assert "cwd_drift" not in executor.run("cd gui/missing")  # in the session folder again
