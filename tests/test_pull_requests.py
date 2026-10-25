"""Pull, and the branch's pull request. Real repositories; `gh` is faked, so
no network and no GitHub account is touched.

Pinned: Pull fast-forwards and refuses what would need a merge; the PR read folds gh's
checks to passed / failed / running and says plainly when gh is missing, signed out, or
the branch has no PR; opening a PR pushes a branch with no upstream first and passes the
person's title, body, base and draft flag through.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from harness.interface.server import SessionManager
from harness.interface.server.manager import pull_requests
from harness.interface.server.manager.pull_requests import fold_checks
from harness.session.sessions import SessionRecord


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for repo in (origin,):
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "Tester")
    (origin / "a.txt").write_text("one\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "init")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "Tester")
    return origin, clone


def _manager(tmp_path, workspace) -> SessionManager:
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive")
    )
    return manager


def test_pull_fast_forwards_and_refuses_a_merge(tmp_path):
    origin, clone = _pair(tmp_path)
    manager = _manager(tmp_path, clone)
    (origin / "a.txt").write_text("two\n", encoding="utf-8")
    _git(origin, "commit", "-qam", "upstream work")
    assert manager.scm_pull("s1") == {"ok": True}
    assert (clone / "a.txt").read_text(encoding="utf-8") == "two\n"

    (origin / "a.txt").write_text("three\n", encoding="utf-8")
    _git(origin, "commit", "-qam", "more upstream")
    (clone / "b.txt").write_text("local\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "local work")
    refused = manager.scm_pull("s1")
    assert refused["ok"] is False and refused["error"]
    assert _git(clone, "log", "-1", "--pretty=%s").strip() == "local work"  # nothing merged

    _git(clone, "switch", "-qc", "unpublished")
    assert "no upstream" in manager.scm_pull("s1")["error"]


def test_the_checks_fold_to_one_verdict():
    rollup = [
        {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "tests", "status": "IN_PROGRESS", "conclusion": ""},
        {"context": "ci/legacy", "state": "PENDING"},
        {"name": "docs", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ]
    folded = fold_checks(rollup)
    assert (folded["passed"], folded["failed"], folded["running"], folded["verdict"]) == (2, 0, 2, "running")
    rollup.append({"name": "e2e", "status": "COMPLETED", "conclusion": "FAILURE", "detailsUrl": "https://ci/e2e"})
    failed = fold_checks(rollup)
    assert failed["verdict"] == "failed" and failed["items"][-1]["url"] == "https://ci/e2e"
    assert fold_checks(None)["verdict"] == "none"


def _fake_gh(monkeypatch, answers):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1:])
        code, out, err = answers(argv[1:])
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(pull_requests.shutil, "which", lambda name: "gh" if name == "gh" else None)
    monkeypatch.setattr(pull_requests, "spawn_run", run)
    return calls


def test_the_pr_read_says_what_stands_in_the_way(tmp_path, monkeypatch):
    _, clone = _pair(tmp_path)
    manager = _manager(tmp_path, clone)
    monkeypatch.setattr(pull_requests.shutil, "which", lambda name: None)
    assert manager.scm_pull_request("s1") == {"available": False, "reason": "the GitHub CLI (gh) is not installed"}

    _fake_gh(monkeypatch, lambda args: (1, "", "To get started with GitHub CLI, please run:  gh auth login"))
    assert "gh auth login" in manager.scm_pull_request("s1")["reason"]
    _fake_gh(monkeypatch, lambda args: (1, "", 'no pull requests found for branch "main"'))
    assert manager.scm_pull_request("s1") == {"available": True, "pr": None}

    view = {
        "number": 12, "title": "Retry uploads", "url": "https://github.com/o/r/pull/12", "state": "OPEN",
        "isDraft": False, "headRefName": "fix/retry", "baseRefName": "main", "reviewDecision": "REVIEW_REQUIRED",
        "statusCheckRollup": [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    _fake_gh(monkeypatch, lambda args: (0, json.dumps(view), ""))
    pr = manager.scm_pull_request("s1")["pr"]
    assert (pr["number"], pr["base"], pr["checks"]["verdict"]) == (12, "main", "passed")


def test_opening_a_pr_pushes_first_and_passes_the_person_s_words(tmp_path, monkeypatch):
    _, clone = _pair(tmp_path)
    _git(clone, "switch", "-qc", "fix/retry")
    (clone / "a.txt").write_text("fixed\n", encoding="utf-8")
    _git(clone, "commit", "-qam", "Retry uploads")
    manager = _manager(tmp_path, clone)
    assert manager.scm_create_pull_request("s1", "  ")["error"] == "a pull request needs a title"

    # gh is faked; the push before it is real (the clone's origin is a local repository).
    calls = _fake_gh(monkeypatch, lambda args: (0, "https://github.com/o/r/pull/13\n", ""))
    made = manager.scm_create_pull_request("s1", "Retry uploads", "Why: 503s.", base="main", draft=True)
    assert made == {"ok": True, "url": "https://github.com/o/r/pull/13"}
    assert _git(clone, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/fix/retry"
    assert calls == [["pr", "create", "--title", "Retry uploads", "--body", "Why: 503s.", "--base", "main", "--draft"]]
