"""The branch picker, against real repositories.

Pinned: the list puts local branches first with their ahead/behind and leaves out a remote
branch that is already checked out; a switch goes to a local branch, tracks a remote one
under its own name, or creates one from HEAD; git's refusal (local changes a switch would
overwrite) comes back in git's words and nothing is forced; a name git would read as an
option or a range is refused before git runs; a new worktree lands beside the repository
without moving the folder's own branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.interface.server import SessionManager
from harness.interface.server.manager.branches import valid_branch


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def _with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A repository cloned from a remote that has a branch the clone never checked out."""
    origin = _repo(tmp_path / "origin")
    _git(origin, "switch", "-qc", "feature/remote-only")
    (origin / "b.txt").write_text("remote\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "remote work")
    _git(origin, "switch", "-q", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "Tester")
    return origin, clone


def _manager(tmp_path) -> SessionManager:
    return SessionManager(workspace=tmp_path, provider=object())


def test_the_list_has_local_branches_first_and_remote_ones_not_yet_checked_out(tmp_path):
    _, clone = _with_remote(tmp_path)
    (clone / "a.txt").write_text("two\n", encoding="utf-8")
    _git(clone, "commit", "-qam", "ahead by one")
    listing = _manager(tmp_path).workspace_branches(str(clone))
    assert listing["is_git"] and listing["current"] == "main" and listing["dirty"] is False
    main = next(b for b in listing["local"] if b["name"] == "main")
    assert main["current"] and main["upstream"] == "origin/main" and main["ahead"] == 1
    # origin/main is the local main's upstream: one row, not two.
    assert [r["name"] for r in listing["remote"]] == ["origin/feature/remote-only"]


def test_switching_goes_local_tracks_remote_or_creates(tmp_path):
    _, clone = _with_remote(tmp_path)
    manager = _manager(tmp_path)
    tracked = manager.workspace_switch_branch(str(clone), "origin/feature/remote-only")
    assert tracked == {"ok": True, "branch": "feature/remote-only"}
    assert _git(clone, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/feature/remote-only"
    assert manager.workspace_switch_branch(str(clone), "main") == {"ok": True, "branch": "main"}
    created = manager.workspace_switch_branch(str(clone), "fix/retry", create=True)
    assert created == {"ok": True, "branch": "fix/retry"}
    assert manager.workspace_branches(str(clone))["current"] == "fix/retry"


def test_a_switch_git_refuses_comes_back_in_git_s_words_and_nothing_moves(tmp_path):
    repo = _repo(tmp_path / "repo")
    _git(repo, "switch", "-qc", "other")
    (repo / "a.txt").write_text("on other\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "other")
    _git(repo, "switch", "-q", "main")
    (repo / "a.txt").write_text("uncommitted on main\n", encoding="utf-8")
    manager = _manager(tmp_path)
    assert manager.workspace_branches(str(repo))["dirty"] is True
    refused = manager.workspace_switch_branch(str(repo), "other")
    assert refused["ok"] is False and "a.txt" in refused["error"]
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "uncommitted on main\n"


def test_a_name_git_would_misread_is_refused_before_git_runs(tmp_path):
    repo = _repo(tmp_path / "repo")
    manager = _manager(tmp_path)
    for bad in ("-D", "--orphan", "a..b", "x@{1}", "sp ace", "ends/", "x.lock", ""):
        assert valid_branch(bad) is False
        assert manager.workspace_switch_branch(str(repo), bad, create=True)["ok"] is False
    assert valid_branch("feature/retry-2") is True
    assert manager.workspace_branches(str(tmp_path / "nowhere")) == {"is_git": False, "local": [], "remote": []}


def test_a_new_worktree_lands_beside_the_repository_and_leaves_its_branch_alone(tmp_path):
    _, clone = _with_remote(tmp_path)
    manager = _manager(tmp_path)
    made = manager.workspace_new_worktree(str(clone), "fix/retry", create=True)
    assert made["ok"] and made["branch"] == "fix/retry"
    path = Path(made["path"])
    assert path.parent == clone.parent and path.name == "clone-fix-retry"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "fix/retry"
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    # A second one for the same slug does not collide; a remote branch is tracked.
    again = manager.workspace_new_worktree(str(clone), "origin/feature/remote-only")
    assert again["ok"] and again["branch"] == "feature/remote-only"
    assert Path(again["path"]).name == "clone-feature-remote-only"
    assert manager.workspace_new_worktree(str(clone), "fix/retry", create=True)["ok"] is False  # exists


def test_a_remote_branch_with_a_local_namesake_is_the_local_row(tmp_path):
    # A local branch of the same name that does not track the remote one (made by hand) is
    # what a switch to that name reaches: listing the remote one too offers a second row
    # that would fail ("a branch named ... already exists").
    _, clone = _with_remote(tmp_path)
    _git(clone, "branch", "feature/remote-only")
    listing = _manager(tmp_path).workspace_branches(str(clone))
    assert "feature/remote-only" in [b["name"] for b in listing["local"]]
    assert listing["remote"] == []
