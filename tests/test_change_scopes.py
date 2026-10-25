"""Review's comparisons and hunk actions, against a real repository.

Pinned: each scope lists exactly its own files and diffs (staged = the index against HEAD,
unstaged = the working tree against the index plus untracked files, branch = against the
merge base, commit = one commit's own change); a hunk action touches only that hunk, and is
refused when the file has moved since the diff was read; a revert goes back to HEAD for a
staged file too; the log pages.
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
    _git(path, "config", "core.autocrlf", "false")
    (path / "a.txt").write_text("".join(f"line {i}\n" for i in range(1, 31)), encoding="utf-8")
    (path / "b.txt").write_text("b\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def _manager(tmp_path, workspace):
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive")
    )
    return manager


def _edit_ends(repo):
    """Change line 2 and line 29 of a.txt: two hunks, far enough apart not to merge."""
    lines = (repo / "a.txt").read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = "line 2 CHANGED\n"
    lines[28] = "line 29 CHANGED\n"
    (repo / "a.txt").write_text("".join(lines), encoding="utf-8")


def test_staged_and_unstaged_scopes_split_the_index_from_the_working_tree(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("changed a\n", encoding="utf-8")
    (repo / "b.txt").write_text("changed b\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)
    manager.scm_stage("s1", ["a.txt"])

    staged = manager.scope_changes("s1", "staged")
    assert [(f["path"], f["staged"]) for f in staged["files"]] == [("a.txt", True)]
    unstaged = manager.scope_changes("s1", "unstaged")
    assert [(f["path"], f["status"], f["staged"]) for f in unstaged["files"]] == [
        ("b.txt", "M", False),
        ("new.txt", "A", False),
    ]
    assert "+changed a" in manager.scope_change_diff("s1", "staged", "a.txt")["patch"]
    # A staged file has no working-tree change left, so it is empty on the unstaged side.
    assert manager.scope_change_diff("s1", "unstaged", "a.txt")["patch"] == ""
    # An untracked file has no patch; its content comes back for the GUI to draw as additions.
    fresh = manager.scope_change_diff("s1", "unstaged", "new.txt")
    assert fresh["untracked"] is True and fresh["content"] == "fresh\n"


def test_branch_scope_is_what_a_pull_request_would_show(tmp_path):
    repo = _repo(tmp_path / "repo")
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    manager = _manager(tmp_path, repo)
    # On the base branch there is nothing to compare, and it says so.
    on_base = manager.scope_changes("s1", "branch")
    assert on_base["files"] == [] and on_base["note"] and on_base["base"] == base

    _git(repo, "checkout", "-qb", "feature")
    (repo / "b.txt").write_text("b on feature\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "feature work")
    (repo / "a.txt").write_text("uncommitted, not in the branch diff\n", encoding="utf-8")
    result = manager.scope_changes("s1", "branch")
    assert result["base"] == base
    assert [(f["path"], f["add"], f["del"]) for f in result["files"]] == [("b.txt", 1, 1)]
    assert "+b on feature" in manager.scope_change_diff("s1", "branch", "b.txt")["patch"]


def test_commit_scope_is_one_commits_own_change_and_refuses_what_is_not_a_commit(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "b.txt").write_text("second\n", encoding="utf-8")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")
    manager = _manager(tmp_path, repo)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    root_sha = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()

    second = manager.scope_changes("s1", f"commit:{sha}")
    assert [(f["path"], f["status"]) for f in second["files"]] == [("b.txt", "M"), ("c.txt", "A")]
    assert "+second" in manager.scope_change_diff("s1", f"commit:{sha}", "b.txt")["patch"]
    # The first commit has no parent; it still reads as everything it added.
    first = manager.scope_changes("s1", f"commit:{root_sha}")
    assert [f["path"] for f in first["files"]] == ["a.txt", "b.txt"]

    for bad in ("commit:nothex!", "commit:deadbeefdeadbeef", "commit:", "wat"):
        assert manager.scope_changes("s1", bad)["files"] == []
        assert manager.scope_changes("s1", bad)["error"]
    assert manager.scope_change_diff("s1", "staged", "../x") == {"ok": False, "error": "invalid path"}


def test_scopes_outside_a_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    manager = _manager(tmp_path, plain)
    assert manager.scope_changes("s1", "staged")["is_git"] is False
    assert manager.scope_change_diff("s1", "staged", "a.txt")["ok"] is False


def test_a_hunk_is_staged_unstaged_and_reverted_on_its_own(tmp_path):
    repo = _repo(tmp_path / "repo")
    _edit_ends(repo)
    manager = _manager(tmp_path, repo)
    header = "@@ -1,5 +1,5 @@"
    patch = manager.scope_change_diff("s1", "unstaged", "a.txt")["patch"]
    headers = [line for line in patch.splitlines() if line.startswith("@@")]
    assert len(headers) == 2 and headers[0].startswith(header)

    # Stage the first hunk only: the index holds line 2, the working tree still has line 29.
    assert manager.scm_hunk("s1", "a.txt", 0, headers[0], "stage") == {"ok": True}
    cached = _git(repo, "diff", "--cached")
    assert "line 2 CHANGED" in cached and "line 29 CHANGED" not in cached
    rest = manager.scope_change_diff("s1", "unstaged", "a.txt")["patch"]
    assert "line 29 CHANGED" in rest and "line 2 CHANGED" not in rest

    # Unstage it again.
    index_header = next(l for l in cached.splitlines() if l.startswith("@@"))
    assert manager.scm_hunk("s1", "a.txt", 0, index_header, "unstage") == {"ok": True}
    assert _git(repo, "diff", "--cached") == ""

    # Revert the last hunk: line 29 goes back, line 2's edit stays.
    headers = [l for l in manager.scope_change_diff("s1", "unstaged", "a.txt")["patch"].splitlines() if l.startswith("@@")]
    assert manager.scm_hunk("s1", "a.txt", 1, headers[1], "revert") == {"ok": True}
    text = (repo / "a.txt").read_text(encoding="utf-8")
    assert "line 2 CHANGED" in text and "line 29 CHANGED" not in text and "line 29\n" in text


def test_a_hunk_action_is_refused_when_the_file_has_moved_on(tmp_path):
    repo = _repo(tmp_path / "repo")
    lines = (repo / "a.txt").read_text(encoding="utf-8").splitlines(keepends=True)
    for at in (1, 14, 28):  # three hunks: lines 2, 15 and 29
        lines[at] = f"line {at + 1} CHANGED\n"
    (repo / "a.txt").write_text("".join(lines), encoding="utf-8")
    manager = _manager(tmp_path, repo)
    patch = manager.scope_change_diff("s1", "unstaged", "a.txt")["patch"]
    headers = [l for l in patch.splitlines() if l.startswith("@@")]
    assert len(headers) == 3
    # The GUI drew the middle hunk as #1. Meanwhile the first one is staged elsewhere, so
    # index 1 now names the LAST hunk: in range, but not the one the person clicked.
    manager.scm_hunk("s1", "a.txt", 0, headers[0], "stage")
    stale = manager.scm_hunk("s1", "a.txt", 1, headers[1], "stage")
    assert stale["ok"] is False and "refresh" in stale["error"]
    cached = _git(repo, "diff", "--cached")
    assert "line 2 CHANGED" in cached and "line 29 CHANGED" not in cached and "line 15 CHANGED" not in cached
    # An index past the last hunk is the same refusal.
    assert manager.scm_hunk("s1", "a.txt", 5, headers[1], "stage")["ok"] is False
    for bad in (
        ("a.txt", -1, headers[0], "stage"),
        ("a.txt", "0", headers[0], "stage"),
        ("a.txt", True, headers[0], "stage"),
        ("a.txt", 0, headers[0], "wat"),
        ("../x", 0, headers[0], "stage"),
    ):
        assert manager.scm_hunk("s1", *bad)["ok"] is False


def test_a_hunk_of_a_crlf_file_still_applies(tmp_path):
    repo = _repo(tmp_path / "repo")
    body = "".join(f"line {i}\r\n" for i in range(1, 31))
    (repo / "w.txt").write_bytes(body.encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "crlf")
    (repo / "w.txt").write_bytes(body.replace("line 2\r\n", "line 2 CHANGED\r\n").encode("utf-8"))
    manager = _manager(tmp_path, repo)
    header = next(l for l in manager.scope_change_diff("s1", "unstaged", "w.txt")["patch"].splitlines() if l.startswith("@@"))
    assert manager.scm_hunk("s1", "w.txt", 0, header, "stage") == {"ok": True}
    assert b"line 2 CHANGED\r\n" in subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached"], capture_output=True, check=True
    ).stdout


def test_revert_goes_back_to_head_for_a_staged_file_and_removes_a_staged_new_one(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "b.txt").write_text("edited and staged\n", encoding="utf-8")
    (repo / "new.txt").write_text("staged and new\n", encoding="utf-8")
    manager = _manager(tmp_path, repo)
    manager.scm_stage("s1", ["b.txt", "new.txt"])
    (repo / "b.txt").write_text("edited again\n", encoding="utf-8")

    assert manager.scm_discard("s1", ["b.txt", "new.txt"]) == {"ok": True}
    assert (repo / "b.txt").read_text(encoding="utf-8") == "b\n"
    assert not (repo / "new.txt").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_the_log_pages_and_says_when_there_may_be_more(tmp_path):
    repo = _repo(tmp_path / "repo")
    for i in range(3):
        (repo / "b.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(repo, "commit", "-qam", f"c{i}")
    manager = _manager(tmp_path, repo)
    page = manager.scm_log("s1", 2)
    assert [c["subject"] for c in page["commits"]] == ["c2", "c1"] and page["more"] is True
    # The base branch is named, so the menu can say "Against main" before it is picked.
    assert page["base"] == _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    everything = manager.scm_log("s1", 50)
    assert len(everything["commits"]) == 4 and everything["more"] is False
    assert len(manager.scm_log("s1", 0)["commits"]) == 1  # at least one


def test_the_routes_reach_the_scopes_and_the_hunk_action(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    repo = _repo(tmp_path / "repo")
    _edit_ends(repo)
    manager = _manager(tmp_path, repo)
    client = TestClient(create_app(manager))
    assert [f["path"] for f in client.get("/v1/sessions/s1/changes?scope=unstaged").json()["files"]] == ["a.txt"]
    assert client.get("/v1/sessions/s1/changes?scope=staged").json()["files"] == []
    patch = client.get("/v1/sessions/s1/changes/diff?path=a.txt&scope=unstaged").json()["patch"]
    header = next(l for l in patch.splitlines() if l.startswith("@@"))
    body = {"path": "a.txt", "index": 0, "header": header, "action": "stage"}
    assert client.post("/v1/sessions/s1/scm/hunk", json=body).json() == {"ok": True}
    assert [f["path"] for f in client.get("/v1/sessions/s1/changes?scope=staged").json()["files"]] == ["a.txt"]
    assert len(client.get("/v1/sessions/s1/scm/log?limit=1").json()["commits"]) == 1
