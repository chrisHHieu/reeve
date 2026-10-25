"""A read-only command reads the session's folders, not the machine.

`readonly.py` checks what a command does, carefully and failing closed, and said nothing of what it
reads. So the session grant this replaced, taken as "stop asking about my project files", also
covered ~/.aws/credentials, another repository's history and Reeve's own secrets file; the floor
that protects those guards them against change, not against being read. A read-only command now
runs unasked in every preset, and the folders it may read are what keep "a read" a read of this
session.
"""

from __future__ import annotations

import pytest

from harness.config.roots import RootDir
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.readonly import is_readonly_command, read_targets


@pytest.fixture()
def session(tmp_path):
    """A session rooted at `repo/`, with a `src/` and a `build.log` in it."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "build.log").write_text("boom", encoding="utf-8")
    return PermissionEngine(workspace_root=root, mode=Mode.WORKSPACE_WRITE)


def runs(engine, command: str) -> bool:
    return engine.evaluate("run_shell", {"command": command}, None).allowed


# -- in the session, and out of it ---------------------------------------------------------------


# The grant came from some fifteen approvals by hand on every security scan; the folders must not
# bring them back.
@pytest.mark.parametrize(
    "command",
    ["cat build.log", "grep AWS_SECRET build.log", "head -n 5 build.log", "ls src", "git status", "git log --oneline", "pwd", "whoami", "cat build.log | grep error", "find . -name '*.py'"],
)
def test_reads_inside_the_session_run_unprompted(session, command):
    assert runs(session, command), command


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.aws/credentials",
        "strings ~/.ssh/id_rsa",
        "cat /etc/shadow",
        "grep -r AWS_SECRET /",
        "find / -name id_rsa",
        "head -n 5 ~/.aws/credentials",
        # Reeve's own secrets: writes are refused in every mode, but reading was never checked, so one
        # click on the old grant put them in the transcript and on to the provider next turn
        "cat ~/.config/Reeve/secrets.json",
        "jq . ~/.config/Reeve/secrets.json",
        "git -C ~/other-private-repo log -p",  # `git -C` is how a command leaves its folder
        "cat ../../../etc/passwd",  # resolved, not matched as text
        "cat ./../../etc/passwd",
    ],
)
def test_a_read_outside_every_root_asks(session, command):
    assert not runs(session, command), command


def test_a_second_root_can_be_read_with_read_access_alone(tmp_path):
    project, reference = tmp_path / "project", tmp_path / "reference"
    for folder in (project, reference):
        folder.mkdir()
    (reference / "spec.md").write_text("x", encoding="utf-8")
    engine = PermissionEngine(workspace_root=project, mode=Mode.WORKSPACE_WRITE, roots=[RootDir(path=project, writable=True), RootDir(path=reference, writable=False)])
    assert runs(engine, f"cat {reference / 'spec.md'}")
    assert not runs(engine, "cat ~/.aws/credentials")


def test_one_stage_out_of_the_session_stops_the_whole_pipeline(session):
    assert runs(session, "cat build.log | grep -i error")
    assert not runs(session, "cat build.log | grep -f ~/.ssh/id_rsa")


def test_a_path_in_the_session_does_not_make_any_command_a_read(session):
    # what the classifier refuses stays refused: executing or reaching the network
    for command in ("python -c 'print(1)'", "curl https://x.io", "pytest -q", "rm build.log"):
        assert not runs(session, command), command


@pytest.mark.parametrize(("mode", "plan"), [(Mode.READ_ONLY, False), (Mode.WORKSPACE_WRITE, True)])
def test_the_read_only_presets_run_a_read_and_refuse_the_rest(tmp_path, mode, plan):
    # Plan and read-only refused every shell command, `git log` too, so a plan could not be drawn
    # from the history it was about. A read is a read in every preset.
    engine = PermissionEngine(workspace_root=tmp_path, mode=mode, plan=plan)
    assert runs(engine, "git log --oneline -5") and runs(engine, "rg TODO | head -20")
    for command in ("rm build.log", "git commit -m x", "cat ~/.aws/credentials"):
        verdict = engine.evaluate("run_shell", {"command": command}, None)
        assert not verdict.allowed and not verdict.needs_user, command  # refused, not asked


# -- where the shell stands ----------------------------------------------------------------------


def test_a_read_is_judged_where_the_shell_stands(tmp_path):
    # `ls` names no operand, and a relative one resolves from the shell's folder: a shell a `cd`
    # took out of the session reads that folder, which is not a read of the session.
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    here = [str(root)]
    engine = PermissionEngine(workspace_root=root, mode=Mode.WORKSPACE_WRITE, shell_cwd=lambda: here[0])
    assert runs(engine, "ls") and runs(engine, "cat a.txt")
    here[0] = str(tmp_path)
    assert not runs(engine, "ls") and not runs(engine, "cat a.txt")  # tmp_path/a.txt now
    # Which verbs read their folder is not worked out one by one: a shell outside the session asks,
    # and moving it there was already the person's call.
    assert not runs(engine, f"cat {root / 'a.txt'}")


def test_a_cd_out_of_the_session_is_not_a_read_of_it(session):
    assert runs(session, "cd src && ls")
    assert not runs(session, "cd .. && ls")
    assert not runs(session, "cd src && cat ../../outside.txt")


def test_terminal_text_is_not_judged_as_a_command(session):
    # what `ls` typed into a terminal means depends on the program the terminal holds
    assert not session.evaluate("terminal_send", {"text": "ls"}, None).allowed


# -- what a command reads ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        ("cat a.txt b.txt", ["a.txt", "b.txt"]),
        ("grep PATTERN a.txt", ["a.txt"]),  # what is searched for is no file
        ("grep -f patterns.txt a.txt", ["patterns.txt", "a.txt"]),  # unless -f gives it as one
        ("head -n 5 a.txt", ["a.txt"]),  # nor is a count
        ("cut -f 1 data.csv", ["data.csv"]),  # -f is a field here
        ("git -C /elsewhere log", ["/elsewhere"]),
        ("git status", []),
        ("echo hello", []),
        ("pwd", []),
        ("jq .foo data.json", ["data.json"]),  # nor is a jq filter
        ("find /tmp -name x", ["/tmp"]),  # the paths end where the predicates begin
        ("nl a.txt | sed -n 2p", ["a.txt"]),
        ("cd src && cat a.txt", ["src", "src/a.txt"]),  # a `cd` is a read, and moves what follows
        ("cd .. && cat secrets", ["..", "../secrets"]),
        ("cd src && cat /etc/hosts", ["src", "/etc/hosts"]),
        ("diff --from-file=/etc/hosts a", ["/etc/hosts", "a"]),  # a flag whose value is a path
    ],
)
def test_read_targets_names_the_files_and_not_the_arguments(command, reads):
    assert read_targets(command) == reads


def test_a_command_the_classifier_refuses_has_no_targets():
    assert not is_readonly_command("curl https://x.io > out")
    assert read_targets("curl https://x.io > out") is None


def test_a_backslash_is_a_separator_not_an_escape():
    # PowerShell keeps the backslashes bash drops; read as POSIX this was a relative name
    assert read_targets(r"cat C:\Users\me\.aws\credentials") == ["C:/Users/me/.aws/credentials"]
