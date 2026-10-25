"""The Review tab's data: git working-tree changes for a session.

Read-only by construction — `status`/`numstat`/`diff` only, never a write. These tests
pin the shapes the rail renders (status letters, counts) and the two safety rules: a
non-git workspace says so, and a path outside the workspace is refused.
"""

from __future__ import annotations

import subprocess

from harness.interface.server import SessionManager
from harness.session.sessions import SessionRecord


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _manager(tmp_path, workspace):
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive")
    )
    return manager


def test_changes_lists_modified_added_and_deleted(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")  # modified
    (repo / "fresh.txt").write_text("hello\n", encoding="utf-8")  # untracked → A
    (repo / "gone.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "gone.txt")
    _git(repo, "commit", "-m", "add gone")
    (repo / "gone.txt").unlink()  # deleted

    result = _manager(tmp_path / "state", repo).session_changes("s1")
    assert result["is_git"] is True
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["fresh.txt"]["status"] == "A"
    assert by_path["gone.txt"]["status"] == "D"


def test_changes_says_which_side_of_the_index_each_file_is_on(tmp_path):
    """The Review tab groups by Staged/Changes, so the porcelain code's two letters
    have to survive the parse: `X` set = staged, `X` blank or `??` = working tree only."""
    repo = _repo(tmp_path / "repo")
    (repo / "both.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "both.txt")
    _git(repo, "commit", "-m", "add both")
    # Now: one unstaged edit, one staged NEW file, one staged modification.
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "staged.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "both.txt").write_text("x\ny\n", encoding="utf-8")
    _git(repo, "add", "both.txt")

    by_path = {
        f["path"]: f for f in _manager(tmp_path / "state", repo).session_changes("s1")["files"]
    }
    assert by_path["tracked.txt"]["staged"] is False
    assert by_path["staged.txt"]["staged"] is True
    assert by_path["both.txt"]["staged"] is True


def test_a_non_git_workspace_says_so(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _manager(tmp_path / "state", plain).session_changes("s1")
    assert result["is_git"] is False
    assert result["files"] == []


def test_diff_of_a_tracked_file_is_a_patch(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    out = _manager(tmp_path / "state", repo).session_change_diff("s1", "tracked.txt")
    assert out["ok"] is True and out["untracked"] is False
    assert "+two" in out["patch"]


def test_diff_of_an_untracked_file_is_its_content(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "fresh.txt").write_text("hello\n", encoding="utf-8")
    out = _manager(tmp_path / "state", repo).session_change_diff("s1", "fresh.txt")
    assert out["ok"] is True and out["untracked"] is True
    assert "hello" in out["content"]


def test_a_path_outside_the_workspace_is_refused(tmp_path):
    repo = _repo(tmp_path / "repo")
    manager = _manager(tmp_path / "state", repo)
    assert manager.session_change_diff("s1", "/etc/passwd")["ok"] is False
    assert manager.session_change_diff("s1", "../../secret")["ok"] is False


def test_reveal_shows_a_workspace_file_and_refuses_the_rest(tmp_path, monkeypatch):
    """`reveal_path` opens the OS file manager — local and non-executing, but a window on
    someone's desktop, so the path is confined to the workspace like a diff read."""
    repo = _repo(tmp_path / "repo")
    manager = _manager(tmp_path / "state", repo)
    opened: list[list[str]] = []
    monkeypatch.setattr(
        "harness.interface.server.manager.reveal.subprocess.Popen",
        lambda argv, **kwargs: opened.append(argv),
    )

    ok = manager.reveal_path("s1", "tracked.txt")
    assert ok["ok"] is True
    assert opened and str(repo / "tracked.txt") in " ".join(opened[0])

    assert manager.reveal_path("s1", "/etc/passwd")["ok"] is False
    assert manager.reveal_path("s1", "../../secret")["ok"] is False
    assert manager.reveal_path("s1", "missing.txt")["ok"] is False
    # Nothing was launched for the refused ones.
    assert len(opened) == 1
