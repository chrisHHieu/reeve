"""Review's Source Control actions: the person's git writes from the GUI.

Stage, unstage, discard, commit and the log, against a real repository. Pinned: each
action changes exactly what it names, discard handles tracked and untracked files, a
commit with nothing staged can take everything, and a path outside the workspace (or a
workspace that is not a repository) is refused before git runs.
"""

from __future__ import annotations

import subprocess

from harness.interface.server import SessionManager
from harness.session.sessions import SessionRecord


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    (path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def _manager(tmp_path, workspace):
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive")
    )
    return manager


def _staged(manager) -> dict[str, bool]:
    return {f["path"]: f["staged"] for f in manager.session_changes("s1")["files"]}


def test_stage_and_unstage_move_exactly_the_named_files(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)

    assert manager.scm_stage("s1", ["tracked.txt"]) == {"ok": True}
    assert _staged(manager) == {"new.txt": False, "tracked.txt": True}
    assert manager.scm_unstage("s1", ["tracked.txt"]) == {"ok": True}
    assert _staged(manager) == {"new.txt": False, "tracked.txt": False}


def test_discard_restores_tracked_and_removes_untracked_only_where_named(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "junk.txt").write_text("scratch\n", encoding="utf-8")
    (repo / "keep.txt").write_text("mine\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)

    assert manager.scm_discard("s1", ["tracked.txt", "junk.txt"]) == {"ok": True}
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "one\n"
    assert not (repo / "junk.txt").exists()
    assert (repo / "keep.txt").exists()  # not named, not touched


def test_commit_takes_the_staged_changes_or_everything(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)

    assert manager.scm_commit("s1", "   ")["error"] == "a commit needs a message"
    manager.scm_stage("s1", ["tracked.txt"])
    result = manager.scm_commit("s1", "only tracked")
    assert result["ok"] and result["commit"]
    assert _staged(manager) == {"new.txt": False}  # the unstaged file stayed out

    assert manager.scm_commit("s1", "the rest", all_changes=True)["ok"]
    assert manager.session_changes("s1")["files"] == []
    log = manager.scm_log("s1")
    assert [c["subject"] for c in log["commits"]][:3] == ["the rest", "only tracked", "init"]
    assert log["branch"] and any("HEAD" in ref for ref in log["commits"][0]["refs"])


def test_paths_outside_the_workspace_and_non_repositories_are_refused(tmp_path):
    repo = _repo(tmp_path / "repo")
    manager = _manager(tmp_path, repo)
    for bad in (["../outside.txt"], ["/etc/passwd"], ["C:/Windows/win.ini"], [], "tracked.txt"):
        assert manager.scm_stage("s1", bad) == {"ok": False, "error": "invalid path"}
    # One bad path refuses the whole list: nothing is half-done.
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    assert manager.scm_discard("s1", ["tracked.txt", "../x"])["ok"] is False
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "two\n"

    plain = tmp_path / "plain"
    plain.mkdir()
    other = _manager(tmp_path / "state2", plain)
    assert other.scm_stage("s1", ["a.txt"]) == {"ok": False, "error": "not a git repository"}
    assert other.scm_log("s1") == {"is_git": False, "commits": []}


def test_the_routes_reach_the_actions(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app

    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)
    client = TestClient(create_app(manager))
    assert client.post("/v1/sessions/s1/scm/stage", json={"paths": ["tracked.txt"]}).json() == {"ok": True}
    committed = client.post("/v1/sessions/s1/scm/commit", json={"message": "via route"}).json()
    assert committed["ok"] is True
    assert client.get("/v1/sessions/s1/scm/log").json()["commits"][0]["subject"] == "via route"
