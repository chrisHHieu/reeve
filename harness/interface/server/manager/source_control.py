"""SourceControlMixin — the Review tab's git writes, made by the PERSON.

These are the actions behind Review's buttons: stage, unstage, revert (a file or one hunk),
commit and the log. They run only because a person pressed a button in the GUI. The agent has no route to them — its git goes through `run_shell` and the permission
engine — so nothing here widens what the model can do.

Every path is workspace-relative and checked the same way `session_change_diff` checks
it (no absolute path, drive letter, leading separator or `..`); a list with one bad path
is refused whole. Discard (revert) is the destructive action: the GUI confirms it, and here
it touches only the paths it names — tracked files go back to HEAD (index and working tree
both), a file that is staged but new is removed, untracked ones are removed with
`git clean -f` on exactly those paths.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run

#: Commits the history list shows by default, and the most it will ever return.
LOG_LIMIT = 50
LOG_MAX = 200
_SEP = "\x1f"
_HUNK_ACTIONS = {"stage": ["--cached"], "unstage": ["--cached", "--reverse"], "revert": ["--reverse"]}


class SourceControlMixin:
    def _scm_root(self, session_id: str) -> Optional[Path]:
        root = self._change_root(session_id)
        if root is None:
            return None
        inside = self._git(root, "rev-parse", "--is-inside-work-tree")
        return root if inside and inside.strip() == "true" else None

    @staticmethod
    def _scm_paths(paths: Any) -> Optional[list[str]]:
        """Workspace-relative paths, or None when any one is not."""
        if not isinstance(paths, list) or not paths:
            return None
        out: list[str] = []
        for raw in paths:
            rel = str(raw or "").strip()
            p = Path(rel)
            if (
                not rel
                or p.is_absolute()
                or rel[:1] in ("/", "\\")
                or re.match(r"^[A-Za-z]:", rel)
                or ".." in p.parts
            ):
                return None
            out.append(rel)
        return out

    @staticmethod
    def _scm_run(root: Path, *args: str, timeout: float = 30.0, env: Optional[dict[str, str]] = None) -> dict[str, Any]:
        """Run one git write; `{ok}` or `{ok: False, error}` with git's own message."""
        try:
            proc = spawn_run(
                ["git", "-C", str(root), *args],
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
            )
        except Exception as exc:
            return {"ok": False, "error": f"git failed: {exc}"}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return {"ok": False, "error": detail[-1][:300] if detail else f"git exited {proc.returncode}"}
        return {"ok": True}

    def scm_stage(self, session_id: str, paths: Any) -> dict[str, Any]:
        root, rels = self._scm_root(session_id), self._scm_paths(paths)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if rels is None:
            return {"ok": False, "error": "invalid path"}
        return self._scm_run(root, "add", "--", *rels)

    def scm_unstage(self, session_id: str, paths: Any) -> dict[str, Any]:
        root, rels = self._scm_root(session_id), self._scm_paths(paths)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if rels is None:
            return {"ok": False, "error": "invalid path"}
        # `restore --staged` needs a HEAD; a repository with no commit yet unstages with rm.
        if self._git(root, "rev-parse", "--verify", "HEAD") is None:
            return self._scm_run(root, "rm", "--cached", "--quiet", "--", *rels)
        return self._scm_run(root, "restore", "--staged", "--", *rels)

    def scm_discard(self, session_id: str, paths: Any) -> dict[str, Any]:
        """Throw away working-tree changes to exactly these paths (the GUI confirmed)."""
        root, rels = self._scm_root(session_id), self._scm_paths(paths)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if rels is None:
            return {"ok": False, "error": "invalid path"}
        untracked = set(
            (self._git(root, "ls-files", "--others", "--exclude-standard", "--", *rels) or "").splitlines()
        )
        tracked = [rel for rel in rels if rel.replace("\\", "/") not in untracked]
        if untracked:
            result = self._scm_run(root, "clean", "-f", "--", *sorted(untracked))
            if not result["ok"]:
                return result
        # Back to the last commit means the index too: restoring only the working tree left a
        # staged file exactly as it was. A tracked path HEAD has never seen (staged, new) has
        # no commit to go back to, so it goes.
        has_head = self._git(root, "rev-parse", "--verify", "--quiet", "HEAD") is not None
        known = [rel for rel in tracked if has_head and self._git(root, "cat-file", "-e", f"HEAD:{rel}") is not None]
        new = [rel for rel in tracked if rel not in known]
        if known:
            result = self._scm_run(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *known)
            if not result["ok"]:
                return result
        if new:
            return self._scm_run(root, "rm", "-f", "--quiet", "--", *new)
        return {"ok": True}

    def scm_hunk(self, session_id: str, path: str, index: Any, header: str, action: str) -> dict[str, Any]:
        """Stage, unstage or revert ONE hunk of one file (`git apply` of just that hunk).

        `stage` and `revert` read the hunk from the working tree's diff, `unstage` from the
        index's. `header` is the hunk's `@@` line as the GUI drew it: if the file has moved
        since, the hunk at `index` is no longer that one and the action is refused rather
        than applied to the wrong lines."""
        root, rels = self._scm_root(session_id), self._scm_paths([path])
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if rels is None:
            return {"ok": False, "error": "invalid path"}
        if action not in _HUNK_ACTIONS or not isinstance(index, int) or isinstance(index, bool) or index < 0:
            return {"ok": False, "error": "invalid hunk"}
        diff = ["diff", "--no-color", "--no-ext-diff", "--no-renames", *(["--cached"] if action == "unstage" else []), "--", rels[0]]
        # Bytes, not text: a CRLF file's lines end in \r\n, and text mode would drop the \r the
        # working tree still has, so the hunk would no longer apply.
        try:
            got = spawn_run(["git", "-C", str(root), *diff], capture_output=True, timeout=30.0)
        except Exception as exc:
            return {"ok": False, "error": f"git failed: {exc}"}
        lines = got.stdout.split(b"\n") if got.returncode == 0 else []
        starts = [i for i, line in enumerate(lines) if line.startswith(b"@@")]
        if index >= len(starts):
            return {"ok": False, "error": "the file changed since the diff was read; refresh"}
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        hunk = lines[starts[index]:end]
        if hunk[0].decode("utf-8", "replace").rstrip("\r") != str(header or "").rstrip("\r"):
            return {"ok": False, "error": "the file changed since the diff was read; refresh"}
        while hunk and hunk[-1] == b"":
            hunk.pop()
        patch = b"\n".join([*lines[:starts[0]], *hunk]) + b"\n"
        try:
            proc = spawn_run(
                ["git", "-C", str(root), "apply", "--recount", *_HUNK_ACTIONS[action], "-"],
                input=patch, capture_output=True, timeout=30.0,
            )
        except Exception as exc:
            return {"ok": False, "error": f"git failed: {exc}"}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            return {"ok": False, "error": detail[-1][:300] if detail else f"git exited {proc.returncode}"}
        return {"ok": True}

    def scm_commit(self, session_id: str, message: str, *, all_changes: bool = False) -> dict[str, Any]:
        """Commit the staged changes — or, with `all_changes`, everything (VS Code's commit
        with nothing staged). Hooks run: this is the person's own commit."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        text = str(message or "").strip()
        if not text:
            return {"ok": False, "error": "a commit needs a message"}
        if all_changes:
            staged = self._scm_run(root, "add", "-A")
            if not staged["ok"]:
                return staged
        result = self._scm_run(root, "commit", "-m", text, timeout=120.0)
        if result["ok"]:
            head = (self._git(root, "rev-parse", "--short", "HEAD") or "").strip()
            result["commit"] = head
        return result

    def _scm_remote(self, root: Path) -> Optional[str]:
        """The remote a push goes to: the branch's upstream remote, else `origin`, else the
        only one. None when the repository has no remote."""
        upstream = (self._git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") or "").strip()
        if upstream and "/" in upstream:
            return upstream.split("/", 1)[0]
        remotes = [r.strip() for r in (self._git(root, "remote") or "").splitlines() if r.strip()]
        if "origin" in remotes:
            return "origin"
        return remotes[0] if len(remotes) == 1 else None

    def scm_push(self, session_id: str) -> dict[str, Any]:
        """Push the current branch — the person's button, with the person's git credentials.

        A branch with an upstream pushes to it; one without is published to the remote and
        tracked (`push -u`), as VS Code's "Publish Branch" does. A terminal prompt for a
        password would hang with nobody to answer it, so it is switched off: a credential
        helper (Git Credential Manager, an ssh agent) still answers, and a missing one fails
        with git's own message."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        branch = (self._git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
        if not branch or branch == "HEAD":
            return {"ok": False, "error": "not on a branch (detached HEAD): nothing to push"}
        remote = self._scm_remote(root)
        if remote is None:
            return {"ok": False, "error": "this repository has no remote to push to"}
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        has_upstream = self._git(root, "rev-parse", "--abbrev-ref", "@{upstream}") is not None
        args = ["push"] if has_upstream else ["push", "-u", remote, branch]
        result = self._scm_run(root, *args, timeout=120.0, env=env)
        if result["ok"]:
            result.update(remote=remote, branch=branch)
        return result

    def scm_log(self, session_id: str, limit: int = LOG_LIMIT) -> dict[str, Any]:
        """The branch and the last `limit` commits, for the commit picker. Read-only."""
        root = self._scm_root(session_id)
        if root is None:
            return {"is_git": False, "commits": []}
        limit = max(1, min(int(limit), LOG_MAX))
        branch = (self._git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
        raw = self._git(
            root,
            "log",
            f"-n{limit}",
            f"--pretty=format:%H{_SEP}%h{_SEP}%an{_SEP}%ar{_SEP}%s{_SEP}%D",
        )
        commits = []
        for line in (raw or "").splitlines():
            parts = line.split(_SEP)
            if len(parts) != 6:
                continue
            full, short, author, when, subject, refs = parts
            commits.append({
                "hash": full,
                "short": short,
                "author": author,
                "when": when,
                "subject": subject,
                "refs": [r.strip() for r in refs.split(",") if r.strip()],
            })
        ahead = self._git(root, "rev-list", "--count", "@{upstream}..HEAD")
        # What the last fetch knows is waiting upstream; Pull fetches before it merges.
        behind = self._git(root, "rev-list", "--count", "HEAD..@{upstream}")
        return {
            "is_git": True,
            "branch": branch,
            "ahead": int(ahead.strip()) if ahead and ahead.strip().isdigit() else None,
            "behind": int(behind.strip()) if behind and behind.strip().isdigit() else None,
            "upstream": (self._git(root, "rev-parse", "--abbrev-ref", "@{upstream}") or "").strip() or None,
            "commits": commits,
            # The branch "Against …" compares with, so the menu can name it before it is picked.
            "base": self._scope_base(root),
            # Where Push would go; None hides the push buttons.
            "remote": self._scm_remote(root),
            # A full page means there may be older commits to ask for.
            "more": len(commits) >= limit and limit < LOG_MAX,
        }
