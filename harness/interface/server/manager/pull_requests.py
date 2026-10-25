"""PullRequestsMixin — Pull, and the branch's pull request with its checks.

- **Pull** brings the branch up to date with its upstream, fast-forward only: a pull that
  would need a merge is refused with git's words, never merged behind the person's back.
- **The pull request** is read and opened through the GitHub CLI (`gh`), with the person's
  own `gh auth`: the branch's open PR (number, title, state, review decision) and its
  checks folded to passed / failed / running, or — when there is none — a way to open one
  (the branch is pushed first if it has no upstream). No token passes through Reeve.

Without `gh`, or without `gh auth`, the PR part says so and asks nothing; the rest of
Review works the same.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run

_PR_FIELDS = "number,title,url,state,isDraft,headRefName,baseRefName,reviewDecision,statusCheckRollup"


def fold_checks(rollup: Any) -> dict[str, Any]:
    """gh's `statusCheckRollup` (check runs and commit statuses) → counts and a verdict."""
    passed = failed = running = 0
    items = []
    for item in rollup if isinstance(rollup, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("context") or "check")
        status = str(item.get("status") or "").upper()
        conclusion = str(item.get("conclusion") or item.get("state") or "").upper()
        if status and status != "COMPLETED" and conclusion in ("", "PENDING", "EXPECTED"):
            state = "running"
        elif conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            state = "passed"
        elif conclusion in ("PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS"):
            state = "running"
        else:
            state = "failed"
        passed += state == "passed"
        failed += state == "failed"
        running += state == "running"
        items.append({"name": name, "state": state, "url": item.get("detailsUrl") or item.get("targetUrl") or None})
    verdict = "failed" if failed else "running" if running else "passed" if passed else "none"
    return {"passed": passed, "failed": failed, "running": running, "verdict": verdict, "items": items}


class PullRequestsMixin:
    @staticmethod
    def _gh(root: Path, *args: str, timeout: float = 60.0) -> tuple[Optional[int], str, str]:
        """(exit code or None when gh is missing, stdout, stderr)."""
        gh = shutil.which("gh")
        if gh is None:
            return None, "", "the GitHub CLI (gh) is not installed"
        try:
            proc = spawn_run(
                [gh, *args], cwd=str(root), capture_output=True, encoding="utf-8", errors="replace",
                timeout=timeout, env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
            )
        except Exception as exc:  # noqa: BLE001
            return 1, "", f"gh failed: {exc}"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def scm_pull(self, session_id: str) -> dict[str, Any]:
        """Fast-forward the branch to its upstream."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if self._git(root, "rev-parse", "--abbrev-ref", "@{upstream}") is None:
            return {"ok": False, "error": "this branch has no upstream to pull from: push it first"}
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        return self._scm_run(root, "pull", "--ff-only", timeout=120.0, env=env)

    def scm_pull_request(self, session_id: str) -> dict[str, Any]:
        """The branch's open pull request and its checks, or what stands in the way."""
        root = self._scm_root(session_id)
        if root is None:
            return {"available": False, "reason": "not a git repository"}
        code, out, err = self._gh(root, "pr", "view", "--json", _PR_FIELDS)
        if code is None:
            return {"available": False, "reason": err}
        if code != 0:
            text = (err or out).strip()
            low = text.lower()
            if "no pull requests found" in low or "no open pull requests" in low:
                return {"available": True, "pr": None}
            if "gh auth login" in low or "not logged" in low or "authentication" in low:
                return {"available": False, "reason": "the GitHub CLI is not signed in: run `gh auth login`"}
            if "not a git repository" in low or "no git remotes" in low or "none of the git remotes" in low:
                return {"available": False, "reason": "this repository has no GitHub remote"}
            return {"available": False, "reason": (text.splitlines() or ["gh failed"])[-1][:300]}
        try:
            data = json.loads(out)
        except ValueError:
            return {"available": False, "reason": "gh answered something unreadable"}
        return {
            "available": True,
            "pr": {
                "number": data.get("number"),
                "title": data.get("title") or "",
                "url": data.get("url") or "",
                "state": data.get("state") or "",
                "draft": bool(data.get("isDraft")),
                "head": data.get("headRefName") or "",
                "base": data.get("baseRefName") or "",
                "review": data.get("reviewDecision") or "",
                "checks": fold_checks(data.get("statusCheckRollup")),
            },
        }

    def scm_create_pull_request(self, session_id: str, title: str, body: str = "", *, base: str = "", draft: bool = False) -> dict[str, Any]:
        """Open a pull request for the current branch (pushing it first when it has no
        upstream), with the person's title and description."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        text = str(title or "").strip()
        if not text:
            return {"ok": False, "error": "a pull request needs a title"}
        if self._git(root, "rev-parse", "--abbrev-ref", "@{upstream}") is None:
            pushed = self.scm_push(session_id)
            if not pushed.get("ok"):
                return {"ok": False, "error": f"the branch could not be pushed: {pushed.get('error')}"}
        args = ["pr", "create", "--title", text, "--body", str(body or "")]
        if base and re.match(r"^[A-Za-z0-9._/\-]{1,120}$", base):
            args += ["--base", base]
        if draft:
            args.append("--draft")
        code, out, err = self._gh(root, *args, timeout=120.0)
        if code is None:
            return {"ok": False, "error": err}
        if code != 0:
            lines = (err or out).strip().splitlines()
            return {"ok": False, "error": (lines[-1] if lines else "gh failed")[:300]}
        url = next((line.strip() for line in out.splitlines() if line.strip().startswith("http")), "")
        return {"ok": True, "url": url}
