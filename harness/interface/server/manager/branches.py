"""BranchesMixin — the person picks the branch a folder is on.

The composer's branch chip and Review's scope menu open one picker: the folder's branches,
local first (with how far each is ahead of / behind its upstream), then the remote ones not
yet checked out; a search; **switch**; **create a branch** from where HEAD is; and, before a
session's first message, **a new worktree** for a branch so the session works in its own
checkout and leaves the folder's branch alone (the worktree option).

These are the person's git writes, reached only from the GUI: the model's git still goes
through `run_shell` and the permission engine. A switch git refuses (local changes the
other branch would overwrite) comes back with git's own sentence, and nothing is stashed
or forced behind the person's back.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run

#: Branch names git accepts, narrowed to what a picker needs: no leading `-` (an option),
#: no `..`, no spaces or control characters, no `@{`, not ending in `.lock` or `/`.
_BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*@\{)(?!.*\.lock$)(?!.*/$)[A-Za-z0-9._/\-]{1,120}$")
_SEP = "\x1f"


def valid_branch(name: str) -> bool:
    return bool(_BRANCH.match(name or ""))


class BranchesMixin:
    @staticmethod
    def _branch_repo(path: str) -> Optional[Path]:
        """The repository root holding `path`, or None when it is not a git folder."""
        folder = Path(str(path or "")).expanduser()
        if not folder.is_dir():
            return None
        try:
            out = spawn_run(
                ["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        top = (out.stdout or "").strip()
        return Path(top) if out.returncode == 0 and top else None

    @staticmethod
    def _branch_git(root: Path, *args: str, timeout: float = 30.0) -> tuple[bool, str]:
        """(ok, stdout or git's last error line)."""
        try:
            proc = spawn_run(
                ["git", "-C", str(root), *args],
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"git failed: {exc}"
        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            # git's refusal is often several lines ("error: …", the files, "Please commit …");
            # the first says what happened, the rest how to fix it.
            text = " ".join(line.strip() for line in lines[:6] if line.strip())
            return False, (text or f"git exited {proc.returncode}")[:500]
        return True, proc.stdout or ""

    def workspace_branches(self, path: str) -> dict[str, Any]:
        """The folder's branches for the picker. Read-only."""
        root = self._branch_repo(path)
        if root is None:
            return {"is_git": False, "local": [], "remote": []}
        ok, current = self._branch_git(root, "rev-parse", "--abbrev-ref", "HEAD")
        current = current.strip() if ok else ""
        fmt = _SEP.join(["%(refname:short)", "%(upstream:short)", "%(upstream:track,nobracket)", "%(committerdate:relative)", "%(contents:subject)"])
        ok, raw = self._branch_git(root, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads")
        local = []
        tracked = set()
        for line in (raw if ok else "").splitlines():
            parts = line.split(_SEP)
            if len(parts) != 5:
                continue
            name, upstream, track, when, subject = parts
            ahead = re.search(r"ahead (\d+)", track)
            behind = re.search(r"behind (\d+)", track)
            if upstream:
                tracked.add(upstream)
            local.append({
                "name": name,
                "current": name == current,
                "upstream": upstream or None,
                "ahead": int(ahead.group(1)) if ahead else 0,
                "behind": int(behind.group(1)) if behind else 0,
                "gone": track.strip() == "gone",
                "when": when,
                "subject": subject,
            })
        ok, raw = self._branch_git(root, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes")
        local_names = {b["name"] for b in local}
        remote = []
        for name in (raw if ok else "").splitlines():
            name = name.strip()
            # `origin/HEAD` is a pointer, not a branch; a remote branch already checked out
            # (tracked, or the same name locally) is the local row.
            if not name or name.endswith("/HEAD") or "/" not in name or name in tracked:
                continue
            if name.split("/", 1)[1] in local_names:
                continue
            remote.append({"name": name, "branch": name.split("/", 1)[1]})
        dirty = bool((self._branch_git(root, "status", "--porcelain", "--untracked-files=no")[1] or "").strip())
        return {
            "is_git": True,
            "root": str(root),
            "current": current if current != "HEAD" else None,
            "detached": current == "HEAD",
            "dirty": dirty,
            "local": local,
            "remote": remote,
        }

    def workspace_switch_branch(self, path: str, branch: str, *, create: bool = False) -> dict[str, Any]:
        """Switch the folder to `branch` — a local one, a remote one (tracked under its own
        name), or, with `create`, a new one from where HEAD is. Uncommitted changes travel
        with the switch when git can carry them; when it cannot, git refuses and says why."""
        root = self._branch_repo(path)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        name = str(branch or "").strip()
        if create:
            if not valid_branch(name):
                return {"ok": False, "error": "not a valid branch name"}
            ok, out = self._branch_git(root, "switch", "-c", name)
        elif "/" in name and self._branch_git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{name}")[0]:
            local = name.split("/", 1)[1]
            if not valid_branch(local):
                return {"ok": False, "error": "not a valid branch name"}
            ok, out = self._branch_git(root, "switch", "--track", name)
        else:
            if not valid_branch(name):
                return {"ok": False, "error": "not a valid branch name"}
            ok, out = self._branch_git(root, "switch", name)
        if not ok:
            return {"ok": False, "error": out}
        ok, now = self._branch_git(root, "rev-parse", "--abbrev-ref", "HEAD")
        return {"ok": True, "branch": now.strip() if ok else name}

    def workspace_new_worktree(self, path: str, branch: str, *, create: bool = False) -> dict[str, Any]:
        """A new checkout of `branch` beside the repository (`<repo>-<branch>`), for a session
        that should not move the folder's own branch. Returns its path."""
        root = self._branch_repo(path)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        name = str(branch or "").strip()
        remote_ref = None
        if not create and "/" in name and self._branch_git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{name}")[0]:
            remote_ref, name = name, name.split("/", 1)[1]
        if not valid_branch(name):
            return {"ok": False, "error": "not a valid branch name"}
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "branch"
        target = root.parent / f"{root.name}-{slug}"
        n = 2
        while target.exists():
            target = root.parent / f"{root.name}-{slug}-{n}"
            n += 1
        if create:
            args = ["worktree", "add", "-b", name, str(target)]
        elif remote_ref:
            args = ["worktree", "add", "--track", "-b", name, str(target), remote_ref]
        else:
            args = ["worktree", "add", str(target), name]
        ok, out = self._branch_git(root, *args, timeout=120.0)
        if not ok:
            return {"ok": False, "error": out}
        return {"ok": True, "path": str(target), "branch": name}
