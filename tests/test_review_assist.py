"""Review's Push and its two model calls, against a real repository and a
scripted provider — no network, no tokens.

Pinned: Push publishes a branch with no upstream and then pushes to it, refuses with no
remote or a detached HEAD, and never waits on a password prompt; "Write it for me" sends
what the commit would hold (the index, or everything) with the recent subjects, and is
metered under its own sentinel; "Review code" keeps only findings placed on a
line the diff shows, reads untracked files as additions, and says when the diff was cut.
"""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

from harness.llm.providers import AssistantTurn
from harness.llm.providers.base import TokenUsage
from harness.interface.server import SessionManager
from harness.interface.server.manager import review_assist
from harness.session.sessions import SessionRecord


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "core.autocrlf", "false")
    (path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "feat: add f")
    return path


def _manager(tmp_path, workspace, reply: str = ""):
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive")
    )
    seen: list[list[dict]] = []

    class _Scripted:
        def complete(self, *, model, messages, tools=None, **settings):
            seen.append(messages)
            return AssistantTurn(text=reply, finish_reason="stop", usage=TokenUsage(input=100, output=20))

    manager.get_engine = lambda session_id, **_: SimpleNamespace(provider=_Scripted(), model="m")
    return manager, seen


def _metered(manager) -> set[str]:
    """The sentinel sessions the usage rollup holds rows for."""
    rows = manager.metrics_store._conn.execute("SELECT DISTINCT session_id FROM model_usage").fetchall()
    return {row[0] for row in rows}


def test_push_publishes_a_new_branch_then_pushes_to_it(tmp_path):
    repo = _repo(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    manager, _ = _manager(tmp_path, repo)
    assert manager.scm_push("s1") == {"ok": False, "error": "this repository has no remote to push to"}

    _git(repo, "remote", "add", "origin", str(remote))
    assert manager.scm_log("s1")["remote"] == "origin"
    first = manager.scm_push("s1")
    assert first == {"ok": True, "remote": "origin", "branch": "main"}
    assert _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/main"

    (repo / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "fix: return 2")
    assert manager.scm_log("s1")["ahead"] == 1
    assert manager.scm_push("s1")["ok"] is True
    assert manager.scm_log("s1")["ahead"] == 0
    assert _git(remote, "log", "-1", "--pretty=%s", "main").strip() == "fix: return 2"


def test_push_refuses_a_detached_head_and_reports_git_s_error(tmp_path):
    repo = _repo(tmp_path / "repo")
    manager, _ = _manager(tmp_path, repo)
    _git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    failed = manager.scm_push("s1")
    assert failed["ok"] is False and failed["error"]  # git's own message, not a hang
    _git(repo, "checkout", "-q", "--detach")
    assert "detached" in manager.scm_push("s1")["error"]


def test_write_it_for_me_describes_what_the_commit_would_hold(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "app.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    (repo / "notes.txt").write_text("not staged\n", encoding="utf-8")
    manager, seen = _manager(tmp_path, repo, reply="fix: return the answer\n")

    assert "nothing to describe" in asyncio.run(manager.scm_suggest_message("s1"))["error"]
    assert seen == []  # no change staged: no call made

    manager.scm_stage("s1", ["app.py"])
    result = asyncio.run(manager.scm_suggest_message("s1"))
    assert result == {"ok": True, "message": "fix: return the answer"}
    sent = seen[-1][-1]["content"]
    assert "return 42" in sent and "not staged" not in sent  # the index only
    assert "feat: add f" in sent  # the style to match

    everything = asyncio.run(manager.scm_suggest_message("s1", all_changes=True))
    assert everything["ok"] and "not staged" in seen[-1][-1]["content"]
    # Metered off-turn under its own sentinel, never in a session's fold.
    purposes = _metered(manager)
    assert "__commit_message__" in purposes


def test_review_keeps_only_findings_the_diff_can_show(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "app.py").write_text("def f():\n    return 1 / 0\n", encoding="utf-8")
    (repo / "new.py").write_text("import os\nos.remove('/')\n", encoding="utf-8")
    reply = (
        "Here you go:\n["
        '{"path": "app.py", "line": 2, "severity": "bug", "body": "Division by zero."},'
        '{"path": "new.py", "line": 2, "severity": "RISK", "body": "Removes the root."},'
        '{"path": "app.py", "line": 99, "severity": "bug", "body": "Not in the diff."},'
        '{"path": "ghost.py", "line": 1, "severity": "bug", "body": "No such file."},'
        '{"path": "new.py", "line": 1, "severity": "whatever", "body": "   "},'
        '{"path": "b/new.py", "line": 1, "severity": "odd", "body": "Unused import."}'
        "]"
    )
    manager, seen = _manager(tmp_path, repo, reply=reply)
    result = asyncio.run(manager.scm_review("s1", "uncommitted"))
    assert result["ok"] and result["truncated"] is False
    assert [(f["path"], f["line"], f["severity"]) for f in result["findings"]] == [
        ("app.py", 2, "bug"),
        ("new.py", 2, "risk"),
        ("new.py", 1, "risk"),  # an unknown severity reads as a risk; "b/" is git's prefix
    ]
    assert "os.remove" in seen[-1][-1]["content"]  # the untracked file went as additions
    purposes = _metered(manager)
    assert "__review__" in purposes


def test_review_says_when_the_diff_was_cut_and_ends_on_a_whole_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x = 1\n" * 40, encoding="utf-8")
    manager, seen = _manager(tmp_path, repo, reply="[]")
    monkeypatch.setattr(review_assist, "REVIEW_DIFF_CHARS", 600)
    result = asyncio.run(manager.scm_review("s1", "uncommitted"))
    assert result == {"ok": True, "findings": [], "truncated": True}
    sent = seen[-1][-1]["content"]
    assert sent.count("diff --git") >= 1 and not sent.rstrip().endswith("+x")  # a whole file


def test_review_of_an_empty_or_unknown_scope_makes_no_call(tmp_path):
    repo = _repo(tmp_path / "repo")
    manager, seen = _manager(tmp_path, repo, reply="[]")
    assert asyncio.run(manager.scm_review("s1", "uncommitted")) == {"ok": True, "findings": [], "truncated": False}
    assert asyncio.run(manager.scm_review("s1", "wat"))["ok"] is False
    assert seen == []


def test_diff_lines_are_the_new_side_lines_a_diff_shows():
    patch = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -10,3 +10,4 @@ def g():\n keep\n-gone\n+new one\n+new two\n keep\n"
        "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
    )
    assert review_assist.diff_lines(patch) == {"x.py": {10, 11, 12, 13}}


def test_push_never_waits_on_a_password_prompt(tmp_path, monkeypatch):
    # Nobody can answer a terminal prompt from the GUI: a push that asked for a password
    # would hang until its timeout. A credential helper still answers.
    from harness.interface.server.manager import source_control

    repo = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    manager, _ = _manager(tmp_path, repo)
    seen = []
    real = source_control.spawn_run

    def spy(argv, **kwargs):
        if "push" in argv:
            seen.append(kwargs.get("env") or {})
        return real(argv, **kwargs)

    monkeypatch.setattr(source_control, "spawn_run", spy)
    manager.scm_push("s1")
    assert seen and seen[0].get("GIT_TERMINAL_PROMPT") == "0"
