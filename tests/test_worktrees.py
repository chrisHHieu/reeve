"""Git worktrees for the `/workspace` sidebar section.

Two layers, tested separately: the porcelain parser (pure — every flag combination),
and the manager/route view over a real repository where git exists. A non-repo is a
first-class answer (`is_git: false`), not an error.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.orchestration.worktrees import list_worktrees, parse_worktree_list

PORCELAIN = """\
worktree /repo/main
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/feature
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature/x

worktree /repo/pinned
HEAD 3333333333333333333333333333333333333333
detached

worktree /repo/locked one
HEAD 4444444444444444444444444444444444444444
branch refs/heads/locked
locked

worktree /repo/bare
bare
"""


def test_parse_worktree_list_reads_every_row_and_flag():
    rows = parse_worktree_list(PORCELAIN)

    assert [r["path"] for r in rows] == [
        "/repo/main",
        "/repo/feature",
        "/repo/pinned",
        "/repo/locked one",
        "/repo/bare",
    ]
    assert rows[0]["branch"] == "main"
    assert rows[1]["branch"] == "feature/x"
    assert rows[2]["detached"] is True and "branch" not in rows[2]
    assert rows[3]["locked"] is True and rows[3]["branch"] == "locked"
    assert rows[4]["bare"] is True and "HEAD" not in rows[4]


def test_parse_worktree_list_ignores_unknown_keys_and_trailing_blank():
    rows = parse_worktree_list("worktree /a\nHEAD abc\nprunable gitdir file points to nothing\ngarbage line\n")
    assert len(rows) == 1
    assert rows[0]["prunable"] is True
    assert rows[0]["prunable_reason"] == "gitdir file points to nothing"


def test_non_git_folder_is_an_empty_answer_not_an_error(tmp_path):
    result = list_worktrees(tmp_path)
    assert result["is_git"] is False
    assert result["worktrees"] == []

    missing = list_worktrees(tmp_path / "nope")
    assert missing["is_git"] is False and missing["worktrees"] == []


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@needs_git
def test_real_repo_lists_primary_and_linked_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git("init", "-q", cwd=repo).returncode == 0
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "init", cwd=repo)
    linked = tmp_path / "linked"
    added = _git("worktree", "add", "-q", str(linked), "-b", "feature", cwd=repo)
    assert added.returncode == 0, added.stderr

    result = list_worktrees(repo)
    assert result["is_git"] is True
    by_name = {w["name"]: w for w in result["worktrees"]}
    assert by_name["repo"]["primary"] is True and by_name["repo"]["current"] is True
    assert by_name["linked"]["branch"] == "feature"
    assert by_name["linked"]["current"] is False

    # Listing FROM the linked worktree marks it current instead.
    from_linked = list_worktrees(linked)
    by_name = {w["name"]: w for w in from_linked["worktrees"]}
    assert by_name["linked"]["current"] is True and by_name["repo"]["current"] is False


@needs_git
def test_route_serves_worktrees_and_non_git_empty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "init", cwd=repo)

    manager = SessionManager(workspace=repo)
    client = TestClient(create_app(manager))

    got = client.get("/v1/workspaces/worktrees", params={"path": str(repo)}).json()
    assert got["is_git"] is True
    assert len(got["worktrees"]) == 1
    assert got["worktrees"][0]["primary"] is True

    plain = tmp_path / "plain"
    plain.mkdir()
    got = client.get("/v1/workspaces/worktrees", params={"path": str(plain)}).json()
    assert got == {"is_git": False, "root": str(plain), "worktrees": []}


def test_route_without_a_workspace_says_so(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    client = TestClient(create_app(manager))
    # A path that is not a directory resolves to no workspace at all.
    got = client.get("/v1/workspaces/worktrees", params={"path": str(tmp_path / "ghost")}).json()
    assert got["is_git"] is False
    assert got["error"] == "no workspace folder"
