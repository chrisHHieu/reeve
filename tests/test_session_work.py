"""The work queue's facts on a session row, against a real repository.

Pinned: what a session changed is summed from its turns' receipts (the agent's own notes left
out, the last check kept, stale when edited after); `open` counts only the changed paths git still
lists, so a committed change is no longer waiting for review; the folder's branch and whether it
is a linked worktree are read from git; a session that is old or on another machine gets no facts;
and the facts are cached, so the five-second list poll does not run git every time.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

from harness.interface.server import SessionManager
from harness.interface.server.manager.session_work import SessionWork, read_receipts, receipt_totals
from harness.session.sessions import SessionRecord


def _git(root, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "core.autocrlf", "false")
    for name in ("a.py", "c.py"):
        (path / name).write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def _receipt(created=(), overwritten=(), stats=(), notes=(), check=None):
    changes = {"created": list(created), "overwritten": list(overwritten), "stats": list(stats)}
    if notes:
        changes["notes"] = list(notes)
    if check:
        changes["check"] = check
    return {"role": "notice", "kind": "changes", "changes": changes}


def test_totals_sum_the_turns_leave_out_notes_and_keep_the_last_check():
    rows = [
        {"role": "user", "content": "fix it"},
        _receipt(overwritten=["/w/a.py"], stats=[{"path": "/w/a.py", "added": 3, "removed": 1}],
                 check={"ok": False, "summary": "1 failed", "edits_since": 0}),
        _receipt(created=["/w/b.py", "/w/memory/notes.md"], overwritten=["/w/a.py"],
                 stats=[{"path": "/w/a.py", "added": 2, "removed": 0}, {"path": "/w/b.py", "added": 10, "removed": 0}],
                 notes=["/w/memory/notes.md"], check={"ok": True, "summary": "12 passed", "edits_since": 1}),
    ]
    totals = receipt_totals(rows)
    assert totals == {
        "paths": ["/w/a.py", "/w/b.py"],
        "files": 2,
        "added": 15,
        "removed": 1,
        "check": {"ok": True, "summary": "12 passed", "stale": True},
    }
    assert receipt_totals([{"role": "user", "content": "hi"}]) is None


def test_only_receipt_lines_are_read(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [json.dumps({"role": "user", "content": "the word changes in a notice"}), "not json {", json.dumps(_receipt(created=["/w/x"]))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert [row["kind"] for row in read_receipts(path)] == ["changes"]


def _manager(tmp_path, repo, messages, *, updated_at=None):
    manager = SessionManager(workspace=tmp_path, provider=object())
    record = SessionRecord(session_id="s1", workspace=str(repo), model="m", mode="interactive", messages=messages)
    manager.session_store.save(record)
    if updated_at is not None:
        with manager.session_store._lock:
            manager.session_store._conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = 's1'", (updated_at,))
            manager.session_store._conn.commit()
    return manager


def _row(manager):
    return next(row for row in manager.list_sessions() if row["session_id"] == "s1")


def test_the_row_says_what_is_still_open_on_which_branch(tmp_path):
    repo = _repo(tmp_path / "shop")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")  # changed and uncommitted
    (repo / "b.py").write_text("y = 1\n", encoding="utf-8")  # new
    messages = [
        {"role": "user", "content": "fix it"},
        _receipt(created=[str(repo / "b.py")], overwritten=[str(repo / "a.py"), str(repo / "c.py")],
                 stats=[{"path": str(repo / "a.py"), "added": 1, "removed": 1}, {"path": str(repo / "b.py"), "added": 1, "removed": 0}]),
    ]
    row = _row(_manager(tmp_path, repo, messages))
    assert row["branch"] == "main"
    assert row["worktree"] is False
    # c.py was changed by the session but is clean in git: committed, so no longer waiting.
    assert row["work"] == {"files": 3, "added": 2, "removed": 1, "open": 2}


def test_a_committed_change_is_no_longer_open(tmp_path):
    repo = _repo(tmp_path / "shop")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "done")
    messages = [_receipt(overwritten=[str(repo / "a.py")], stats=[{"path": str(repo / "a.py"), "added": 1, "removed": 1}])]
    assert _row(_manager(tmp_path, repo, messages))["work"]["open"] == 0


def test_a_linked_worktree_says_so(tmp_path):
    repo = _repo(tmp_path / "shop")
    linked = tmp_path / "shop-fix"
    _git(repo, "worktree", "add", "-q", "-b", "fix/retry", str(linked))
    row = _row(_manager(tmp_path, linked, [{"role": "user", "content": "hi"}]))
    assert row["branch"] == "fix/retry"
    assert row["worktree"] is True
    assert "work" not in row  # it changed nothing


def test_an_old_session_gets_no_facts(tmp_path):
    repo = _repo(tmp_path / "shop")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    row = _row(_manager(tmp_path, repo, [_receipt(created=[str(repo / "b.py")])], updated_at=old))
    assert "work" not in row and "branch" not in row


def test_facts_are_cached_between_polls(tmp_path):
    repo = _repo(tmp_path / "shop")
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(json.dumps(_receipt(overwritten=[str(repo / "a.py")])) + "\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def git(root, *args, timeout=15.0):
        calls.append(args[:1])
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout

    now = [0.0]
    work = SessionWork(git, lambda sid: transcript, clock=lambda: now[0])
    stamp = datetime.now(timezone.utc).isoformat()
    first = work.fields("s1", str(repo), stamp, local=True)
    assert first["work"]["open"] == 0
    work.fields("s1", str(repo), stamp, local=True)
    assert len(calls) == 2  # rev-parse and status, once
    now[0] += 20  # past the folder cache
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    assert work.fields("s1", str(repo), stamp, local=True)["work"]["open"] == 1
    assert work.fields("s1", str(repo), stamp, local=False) == {}
