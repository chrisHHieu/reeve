"""Which shell commands only read (`safety/readonly`; asked for by the owner 2026-08-11, the default
now).

The classifier fails closed by design: reads of local files and pipelines of them, nothing else. A
read it misses costs one approval by hand; a write it lets through is a side effect nobody looked
at, and what it accepts runs unasked in every preset. The commands are grouped by why.
"""

from __future__ import annotations

import pytest

from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.readonly import is_readonly_command

READS = {
    "files and folders": ("ls -la", "cat README.md", "find . -name '*.py'", "wc -l file.txt | sort", "head -20 x | tail -5 | uniq -c"),
    "searching": ("grep -rn 'pattern' src", "rg --json TODO", "LC_ALL=C grep -c uses .github/workflows/ci.yml"),
    "printing a part": ("nl -ba tests/test_x.py | sed -n '320,345p'", "nl -ba a.py | sed -E \"s/'[^']*'/'[REDACTED]'/g\"", "awk '{print $1}' data.txt", "jq '.results | length' /tmp/report.json", "printf 'a: '"),
    "git that only looks": (
        "git log --oneline -5", "git diff main...HEAD", "git status", "git -C /tmp/repo log -1", "git branch --show-current",
        "git stash list", "git config --get user.name", "git remote -v", "LC_ALL=C git log --oneline",
    ),
    "finding a program": ("command -v semgrep",),
    # how a model writes its reads: several in a row, a `cd` first, stderr joined in
    "as models write them": ("git status && git diff --stat", "git log --oneline -5; git status --short", "cd src && git log -3", "git log -5 2>&1 | tail -20"),
}

NOT_READS = {
    "nothing at all": ("",),
    "deletes": ("rm -rf /", "find . -delete", "find . -exec rm {} ;", "xargs rm", "env FOO=1 rm x"),
    "redirects to a file": ("cat a > b", "cat a >> b", "grep x f 2>/dev/null", "ls 2>/dev/null", "tee /tmp/x", "ls | tee /tmp/x"),
    "a second command that is not a read": ("ls; rm -rf /", "ls && touch x", "git status; rm x", "git status && git add .", "ls & rm x", "git status\nrm -rf ~", "git status\r\nrm -rf ~"),
    "an incomplete pipe": ("ls |",),
    "runs what it substitutes": ("cat `whoami`", "cat $(secret)", "grep '$(x)' file", "ls (Remove-Item x)", "(rm x)"),
    "reaches the network": ("curl https://api.github.com/repos/x", "wget http://x", "ssh host ls"),
    "an interpreter or a shell": ("python3 -c 'print(1)'", "bash -c ls", "sudo cat /etc/shadow"),
    "sed or awk that writes or runs": (
        "sed -i 's/a/b/' f", "sed -n 'w /tmp/x' f", "sed -f script.sed f", "sed 's/a/b/e' f", "sed '1e ls' f", "sed 'r /etc/passwd' f", "sed 's/a/b/w out' f",
        "awk '{print > \"f\"}' x", "awk 'BEGIN{system(\"rm x\")}'", "awk '{print | \"sh\"}' x", "awk '{ \"date\" | getline d }' x", "awk '{print ENVIRON[\"KEY\"]}' x",
    ),
    "git that changes or runs something": (
        "git push origin main", "git branch new-branch", "git tag v1", "git stash", "git config user.name evil", "git -c core.pager='touch x' log",
        "git log --output=/tmp/f", "GIT_EXTERNAL_DIFF=sh git diff", "GIT_PAGER='sh -c x' git log", "git diff --no-index /etc/passwd a",
        "git diff --ext-diff", "git grep -O vim TODO",
    ),
    "shows the environment": ("env", "echo $OPENAI_API_KEY", "cat \"$HOME/.netrc\"", "echo $env:OPENAI_API_KEY", "jq -n env"),
    "a folder nobody can name": ("cd - && ls", "cd && ls"),
    "a stream join inside quotes": ("grep '2>&1' /etc/passwd",),
    "a flag or operand that writes or runs": (
        "sort -o out a", "sort -ro out a", "sort --compress-program=sh a", "uniq a out", "xxd a out", "xxd -r a",
        "rg --pre sh TODO", "ugrep --filter='*:sh' x", "file -C -m magic", "date -s 2020-01-01",
    ),
    "a program named by its path": ("/tmp/evil/cat file", "C:\\tools\\cat.exe a", "c\\at a.txt", "ls | .\\evil"),
}


def _cases(groups):
    return [pytest.param(command, id=f"{why}: {command!r}") for why, commands in groups.items() for command in commands]


@pytest.mark.parametrize("command", _cases(READS))
def test_a_read_is_known_as_one(command):
    assert is_readonly_command(command) is True


@pytest.mark.parametrize("command", _cases(NOT_READS))
def test_anything_else_is_not(command):
    assert is_readonly_command(command) is False


class _ShellMetadata:
    category = "shell"
    risk_level = "high"
    capabilities = ["exec"]


def test_what_is_known_as_a_read_runs_and_the_rest_still_asks(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE)
    for read in ("ls -la", "git log -1"):
        assert engine.evaluate("run_shell", {"command": read}, _ShellMetadata()).allowed
    for other in ("rm -rf x", "curl https://x"):  # a write, the network
        assert engine.evaluate("run_shell", {"command": other}, _ShellMetadata()).needs_user


def test_a_saved_readonly_grant_from_an_older_session_is_ignored(tmp_path):
    # older sessions carry `"readonly": true`; what it allowed is the default now
    from harness.interface.server.manager.session_persist import SessionPersistMixin, grants_of

    class _Engine:
        permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE)

    SessionPersistMixin._apply_grants(_Engine, {"tools": [], "commands": ["make"], "readonly": True})
    assert grants_of(_Engine) == {"tools": [], "commands": ["make"]}
