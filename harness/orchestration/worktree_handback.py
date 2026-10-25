"""Handing an isolated child's work back: what it changed, apply it, or drop it.

A child that worked in its own git worktree leaves its work THERE, on a branch
nothing has merged. Child worktrees stopped at "the worktree is the deliverable; merging is the
person's decision" — which for a person who does not use git is a decision they cannot
act on. These are the three verbs the Review surface offers instead:

- `worktree_changes`: every file the child's copy differs in from the commit it started
  at, committed or not, tracked or new (the copy's index is staged whole to read it);
- `apply_child_worktree`: that difference as ONE patch onto the parent's folder, with
  `git apply`, which applies all of it or none of it. A patch that does not fit what is in
  the folder now is refused and the copy is kept — no half-applied change, no conflict
  markers in the person's files. Applied work arrives as ordinary uncommitted changes,
  where Review already shows it and Undo of a file is `git checkout`;
- `discard_child_worktree`: the copy and its branch, removed.

`find_child_worktree` reads a child's worktree back from disk — the path and the branch
encode the child id — because the manager's own map is in memory and empty after a restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run
from .worktrees import CHILD_BRANCH_PREFIX, CHILD_WORKTREE_DIR, repo_root


def _git(cwd: str, *args: str, data: Optional[bytes] = None, timeout: float = 60) -> Any:
    return spawn_run(["git", "-C", cwd, *args], capture_output=True, input=data, timeout=timeout)


def _text(raw: Any) -> str:
    return (raw or b"").decode("utf-8", errors="replace").strip()


def find_child_worktree(workspace: str | Path, child_id: str) -> Optional[dict[str, Any]]:
    """`{path, branch, base_head, root}` for a child's worktree that still exists, or None."""
    root = repo_root(workspace)
    if root is None:
        return None
    path = root / CHILD_WORKTREE_DIR / f"subagent-{child_id}"
    if not path.is_dir():
        return None
    branch = f"{CHILD_BRANCH_PREFIX}{child_id}"
    try:
        # Where the child's branch left the parent's history: the commit it was created at,
        # however far either side has moved since.
        base = _git(str(root), "merge-base", "HEAD", branch, timeout=15)
        if base.returncode != 0 or not _text(base.stdout):
            return None
    except Exception:  # noqa: BLE001 — unreadable means "no worktree to offer"
        return None
    return {"path": str(path), "branch": branch, "base_head": _text(base.stdout), "root": str(root)}


def _staged_patch(info: dict[str, Any]) -> tuple[Optional[bytes], str]:
    """The copy's whole difference from its base as a patch, or `(None, reason)`."""
    path, base = str(info.get("path") or ""), str(info.get("base_head") or "")
    if not path or not base:
        return None, "no worktree recorded"
    staged = _git(path, "add", "-A")
    if staged.returncode != 0:
        return None, _text(staged.stderr) or "git add failed"
    patch = _git(path, "diff", "--cached", "--binary", "--no-color", base)
    if patch.returncode != 0:
        return None, _text(patch.stderr) or "git diff failed"
    return patch.stdout or b"", ""


def worktree_changes(info: dict[str, Any]) -> dict[str, Any]:
    """`{files: [{path, change, added, removed}]}` — or `{error}`; never raises."""
    path, base = str(info.get("path") or ""), str(info.get("base_head") or "")
    try:
        _, problem = _staged_patch(info)
        if problem:
            return {"error": problem}
        kinds = _git(path, "diff", "--cached", "--name-status", "--no-renames", base, timeout=30)
        stats = _git(path, "diff", "--cached", "--numstat", "--no-renames", base, timeout=30)
        if kinds.returncode != 0 or stats.returncode != 0:
            return {"error": "inspection failed"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    change = {"A": "created", "D": "deleted"}
    files: dict[str, dict[str, Any]] = {}
    for line in _text(kinds.stdout).splitlines():
        code, _, name = line.partition("\t")
        if name:
            files[name] = {"path": name, "change": change.get(code[:1], "modified"), "added": 0, "removed": 0}
    for line in _text(stats.stdout).splitlines():
        added, removed, name = (line.split("\t") + ["", ""])[:3]
        if name in files:  # a binary file counts as "-": no line counts to show
            files[name]["added"] = int(added) if added.isdigit() else 0
            files[name]["removed"] = int(removed) if removed.isdigit() else 0
    return {"files": list(files.values())}


def discard_child_worktree(info: dict[str, Any]) -> dict[str, Any]:
    """Remove the copy and its branch, work and all: `{removed: bool, reason?}`."""
    path, root = str(info.get("path") or ""), str(info.get("root") or "")
    if not path or not root:
        return {"removed": False, "reason": "no worktree recorded"}
    try:
        # From the repo root, never from inside the worktree (Windows cannot delete a
        # process's own working directory); `--force` because the work is what is dropped.
        removed = _git(root, "worktree", "remove", "--force", path)
        if removed.returncode != 0:
            return {"removed": False, "reason": _text(removed.stderr) or "git worktree remove failed"}
        branch = str(info.get("branch") or "")
        if branch:
            _git(root, "branch", "-D", branch, timeout=15)
    except Exception as exc:  # noqa: BLE001
        return {"removed": False, "reason": str(exc)}
    return {"removed": True}


def apply_child_worktree(info: dict[str, Any]) -> dict[str, Any]:
    """The copy's work onto the parent's folder, all of it or none: `{applied, files}` or
    `{applied: False, reason}`. The copy is removed only after the patch went in."""
    root = str(info.get("root") or "")
    try:
        patch, problem = _staged_patch(info)
        if patch is None:
            return {"applied": False, "reason": problem}
        if not patch.strip():
            return {"applied": False, "reason": "the copy holds no changes"}
        listed = worktree_changes(info).get("files") or []
        # `git apply` is atomic: a hunk that does not fit leaves the folder untouched.
        done = _git(root, "apply", "--whitespace=nowarn", "--binary", "-", data=patch)
        if done.returncode != 0:
            return {
                "applied": False,
                "reason": (
                    "the changes do not fit what is in the folder now (it changed since "
                    "the copy was made): " + (_text(done.stderr).splitlines() or ["git apply failed"])[0]
                ),
            }
    except Exception as exc:  # noqa: BLE001
        return {"applied": False, "reason": str(exc)}
    discard_child_worktree(info)
    return {"applied": True, "files": listed}


__all__ = [
    "apply_child_worktree",
    "discard_child_worktree",
    "find_child_worktree",
    "worktree_changes",
]
