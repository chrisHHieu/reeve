"""Git worktrees for the current workspace.

The sidebar can list a project's linked git worktrees and open a
session in one (: enabled only when `currentProject()?.vcs ===
"git"`). This is the honest v1 of that surface: `git worktree list --porcelain` parsed
into rows, and "open a session here" reuses the app's ordinary workspace switch — no
separate workspace concept, no create/remove verbs yet (deferred; see the sibling
note in the report). A non-repo (or a folder git refuses) answers `is_git:
false` with an empty list, never an error the caller has to interpret.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Optional
from harness.platform.spawn import run as spawn_run

#: `git worktree list --porcelain` blocks: `worktree <path>`, optional `HEAD <sha>`,
#: optional `branch <ref>`, then bare/detached/locked/prunable flags. Blocks end at a
#: blank line (and the last one at EOF). Parsing is pure so the shape is testable
#: without a repository.
_PORCELAIN_FLAGS = ("bare", "detached", "locked", "prunable")


def parse_worktree_list(text: str) -> list[dict[str, Any]]:
    """Parse porcelain output into rows; unknown keys are ignored, not fatal."""
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = stripped.partition(" ")
        value = value.strip()
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            # `refs/heads/feature/x` reads as `feature/x`; anything else (a tag, a
            # remote ref in a detached entry) is kept as git spelled it.
            prefix = "refs/heads/"
            current["branch"] = value[len(prefix) :] if value.startswith(prefix) else value
        elif key in _PORCELAIN_FLAGS:
            current[key] = True
            if value:
                current[f"{key}_reason"] = value
    if current:
        rows.append(current)
    return rows


def _canonical(path: str | Path) -> str:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except OSError:
        return os.path.normcase(str(path))


def list_worktrees(workspace: str | Path) -> dict[str, Any]:
    """The workspace's git worktrees, primary first.

    Shape: `{is_git, root, worktrees: [{path, name, head?, branch?, bare?, detached?,
    locked?, prunable?, primary, current}]}`. `is_git: false` means the folder is not
    a repository (or git is not installed) — not that the call failed.
    """
    root = Path(workspace).expanduser()
    if not str(workspace).strip() or not root.is_dir():
        return {
            "is_git": False,
            "root": str(root) if str(workspace).strip() else "",
            "worktrees": [],
            "error": "no workspace folder",
        }
    try:
        out = spawn_run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 — git missing/hung is "not a repo" to the UI
        return {"is_git": False, "root": str(root), "worktrees": [], "error": str(exc)}
    if out.returncode != 0:
        return {"is_git": False, "root": str(root), "worktrees": []}

    rows = parse_worktree_list(out.stdout)
    if not rows:
        return {"is_git": False, "root": str(root), "worktrees": []}
    here = _canonical(root)
    out_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        path = str(row.get("path", ""))
        out_rows.append(
            {
                **row,
                "name": Path(path).name or path,
                "primary": index == 0,
                "current": _canonical(path) == here,
            }
        )
    return {"is_git": True, "root": str(root), "worktrees": out_rows}


# -- per-child worktrees ----------------------------------------------------

#: Where child worktrees live inside the repository, and the branch each one gets. The
#: path encodes the child id, so a worktree can be traced back to its session without a
#: schema field, and `.worktrees/` is ignored by git when the repo's `.gitignore` says so
#: (we also add it to the exclude file at creation).
CHILD_WORKTREE_DIR = ".worktrees"
CHILD_BRANCH_PREFIX = "reeve-sub/"


def repo_root(workspace: str | Path) -> Optional[Path]:
    """The git top-level containing *workspace*, or None (not a repo / git missing)."""
    try:
        out = spawn_run(
            ["git", "-C", str(Path(workspace).expanduser()), "rev-parse", "--show-toplevel"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(out.stdout.strip())


def create_child_worktree(workspace: str | Path, child_id: str) -> dict[str, Any]:
    """One worktree for one subagent, at `<repo>/.worktrees/subagent-<id>`.

    Returns `{path, branch, base_head, root}` or `{"error": …}`; never raises — the
    caller falls back to the parent's workspace, because isolation that cannot be set up
    must not cost the spawn. A repository with no commit yet has no base to branch from
    and answers an error (the fallback applies).
    """
    root = repo_root(workspace)
    if root is None:
        return {"error": "not a git repository"}
    try:
        head = spawn_run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if head.returncode != 0 or not head.stdout.strip():
        return {"error": "repository has no commit to branch from"}
    base_head = head.stdout.strip()
    path = root / CHILD_WORKTREE_DIR / f"subagent-{child_id}"
    branch = f"{CHILD_BRANCH_PREFIX}{child_id}"
    try:
        # `.worktrees/` is ours and must not show up as untracked noise in `git status`:
        # the exclude file is the per-clone, non-committed way to say so.
        exclude = Path(
            spawn_run(
                ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            ).stdout.strip()
            or ""
        )
        if exclude.is_dir():
            exclude_file = exclude / "info" / "exclude"
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            if exclude_file.exists() and CHILD_WORKTREE_DIR + "/" not in exclude_file.read_text(
                encoding="utf-8", errors="replace"
            ):
                with exclude_file.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n{CHILD_WORKTREE_DIR}/\n")
    except Exception:  # noqa: BLE001 — best-effort; the worktree itself matters
        pass
    try:
        created = spawn_run(
            [
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "-b",
                branch,
                str(path),
                base_head,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if created.returncode != 0:
        return {"error": (created.stderr or created.stdout or "git worktree add failed").strip()}
    return {"path": str(path), "branch": branch, "base_head": base_head, "root": str(root)}


def remove_child_worktree(info: dict[str, Any]) -> dict[str, Any]:
    """Remove a child worktree ONLY on proof: zero commits since its base, AND clean.

    Anything else is kept — a dirty worktree or one with commits is work product the
    person may still want, and guessing it away would be the git-state equivalent of the
    silent data loss the compaction ADRs exist to prevent. A failed inspection keeps it
    too (`{"removed": False, "reason": …}`), never raises.
    """
    path = str(info.get("path") or "")
    base_head = str(info.get("base_head") or "")
    root = str(info.get("root") or "")
    if not path or not base_head or not root:
        return {"removed": False, "reason": "no worktree recorded"}
    try:
        status = spawn_run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if status.returncode != 0:
            return {"removed": False, "reason": "inspection failed"}
        if status.stdout.strip():
            return {"removed": False, "reason": "worktree has uncommitted changes"}
        count = spawn_run(
            ["git", "-C", path, "rev-list", "--count", f"{base_head}..HEAD"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if count.returncode != 0:
            return {"removed": False, "reason": "inspection failed"}
        if int(count.stdout.strip() or "0") != 0:
            return {"removed": False, "reason": "worktree has commits"}
        removed = spawn_run(
            # From the REPO root, never from inside the worktree: a process cannot delete
            # its own working directory on Windows ("Permission denied" — measured).
            ["git", "-C", root, "worktree", "remove", path],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if removed.returncode != 0:
            return {
                "removed": False,
                "reason": (removed.stderr or "git worktree remove failed").strip(),
            }
        branch = str(info.get("branch") or "")
        if branch:
            # Same proof as the removal: the branch points at the base commit.
            spawn_run(
                ["git", "-C", root, "branch", "-D", branch],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
    except Exception as exc:  # noqa: BLE001
        return {"removed": False, "reason": str(exc)}
    return {"removed": True}
