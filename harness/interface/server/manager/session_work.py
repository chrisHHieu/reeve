"""What a session did to its folder, for the work queue.

The GUI's frame is a queue of work grouped by what each session needs from the person (owner,
2026-10-13): waiting on them, running, waiting for review, done. "Waiting for review" is the one
group the session list could not say: it needs to know what a session changed, and whether that
change is still uncommitted where it was made.

Both are read from records that already exist, never guessed:

- what the session changed is the sum of its turns' receipts, the `changes` notices every turn
  ending saves into the transcript, with the last check that ran;
- whether it is still open is the folder's own `git status`: a changed path that git still lists.

The list is asked for every five seconds, so both are cached: a session's totals until its
transcript file changes, and a folder's git facts for `GIT_TTL` seconds. Only sessions active in
the last `RECENT_DAYS` days are read at all; an older one is "earlier" whatever it did.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

RECENT_DAYS = 7
GIT_TTL = 15.0
GIT_TIMEOUT = 5.0

Git = Callable[..., Optional[str]]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def receipt_totals(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The session's changes over every `changes` notice in `rows`, or None when it changed nothing.

    `paths` are the files the person would review (the agent's own notes are left out, as the
    receipt itself sets them apart), each counted once with its lines summed over the turns.
    `check` is the last check any turn ran, `stale` when files were edited after it."""
    files: dict[str, list[int]] = {}
    check: Optional[dict[str, Any]] = None
    for row in rows:
        if row.get("role") != "notice" or row.get("kind") != "changes":
            continue
        receipt = row.get("changes")
        if not isinstance(receipt, dict):
            continue
        notes = {_norm(p) for p in receipt.get("notes") or [] if isinstance(p, str)}
        stats = {s.get("path"): s for s in receipt.get("stats") or [] if isinstance(s, dict)}
        touched = [*(receipt.get("created") or []), *(receipt.get("overwritten") or []), *(receipt.get("deleted") or [])]
        for path in [*touched, *(p for p in stats if p not in touched)]:
            if not isinstance(path, str) or not path or _norm(path) in notes:
                continue
            slot = files.setdefault(path, [0, 0])
            stat = stats.get(path) or {}
            slot[0] += int(stat.get("added") or 0)
            slot[1] += int(stat.get("removed") or 0)
        if isinstance(receipt.get("check"), dict):
            check = receipt["check"]
    if not files:
        return None
    out: dict[str, Any] = {
        "paths": sorted(files),
        "files": len(files),
        "added": sum(a for a, _ in files.values()),
        "removed": sum(r for _, r in files.values()),
    }
    if check is not None:
        out["check"] = {
            "ok": bool(check.get("ok")),
            "summary": str(check.get("summary") or "")[:80],
            "stale": bool(check.get("edits_since")),
        }
    return out


def read_receipts(path: Path) -> list[dict[str, Any]]:
    """The `changes` notices of one transcript file. Only the lines that can hold one are parsed:
    a long conversation is megabytes, and its receipts are a few hundred bytes of it."""
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"changes"' not in line or '"notice"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def git_folder(workspace: str, git: Git) -> Optional[dict[str, Any]]:
    """The repository a folder is in: its top, its branch (None when detached), and whether the
    folder is a linked worktree. None when the folder is not in one."""
    out = git(Path(workspace), "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir", "--abbrev-ref", "HEAD", timeout=GIT_TIMEOUT)
    lines = [line.strip() for line in (out or "").splitlines()]
    if len(lines) < 4 or not lines[0]:
        return None
    top, git_dir, common, branch = lines[:4]
    resolve = lambda p: _norm(str((Path(workspace) / p).resolve())) if not os.path.isabs(p) else _norm(p)
    return {"top": top, "branch": None if branch == "HEAD" else branch, "worktree": resolve(git_dir) != resolve(common)}


def git_open(top: str, git: Git) -> set[str]:
    """The paths git still lists as changed or new under `top`, normalised for comparison."""
    out = git(Path(top), "status", "--porcelain=v1", "-uall", timeout=GIT_TIMEOUT) or ""
    paths: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].split(" -> ")[-1].strip().strip('"')
        paths.add(_norm(os.path.join(top, raw)))
    return paths


def recent(updated_at: Optional[str], days: int = RECENT_DAYS) -> bool:
    if not updated_at:
        return False
    try:
        when = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= datetime.now(timezone.utc) - timedelta(days=days)


class SessionWork:
    """The caches behind the queue's facts, one per manager."""

    def __init__(self, git: Git, transcript_of: Callable[[str], Path], clock: Callable[[], float] = time.monotonic) -> None:
        self._git = git
        self._transcript_of = transcript_of
        self._clock = clock
        self._totals: dict[str, tuple[tuple[int, int], Optional[dict[str, Any]]]] = {}
        self._folders: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}
        self._open: dict[str, tuple[float, set[str]]] = {}

    def _totals_of(self, session_id: str) -> Optional[dict[str, Any]]:
        path = self._transcript_of(session_id)
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        hit = self._totals.get(session_id)
        if hit is not None and hit[0] == key:
            return hit[1]
        totals = receipt_totals(read_receipts(path))
        self._totals[session_id] = (key, totals)
        return totals

    def _folder(self, workspace: str) -> Optional[dict[str, Any]]:
        now = self._clock()
        hit = self._folders.get(workspace)
        if hit is not None and now - hit[0] < GIT_TTL:
            return hit[1]
        facts = git_folder(workspace, self._git) if os.path.isdir(workspace) else None
        self._folders[workspace] = (now, facts)
        return facts

    def _open_paths(self, top: str) -> set[str]:
        now = self._clock()
        hit = self._open.get(top)
        if hit is not None and now - hit[0] < GIT_TTL:
            return hit[1]
        paths = git_open(top, self._git)
        self._open[top] = (now, paths)
        return paths

    def fields(self, session_id: str, workspace: str, updated_at: Optional[str], *, local: bool) -> dict[str, Any]:
        """The queue's fields for one row: `branch` and `worktree` of its folder, and `work`, what
        it changed with how much of it is still `open`. Empty for a session that is not recent or
        not on this machine."""
        if not local or not workspace or not recent(updated_at):
            return {}
        out: dict[str, Any] = {}
        folder = self._folder(workspace)
        if folder is not None:
            out["branch"] = folder["branch"]
            out["worktree"] = folder["worktree"]
        totals = self._totals_of(session_id)
        if totals is None:
            return out
        work = {k: v for k, v in totals.items() if k != "paths"}
        if folder is not None:
            changed = {_norm(p) for p in totals["paths"]}
            work["open"] = len(changed & self._open_paths(folder["top"]))
        out["work"] = work
        return out
