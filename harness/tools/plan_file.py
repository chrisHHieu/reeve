"""The session's plan file — durable, model-readable, and private to the session.

`propose_plan` carries a plan as tool-call text, and approval flips the mode; the plan
then survives compaction through `CompactionState.approved_plan`. What a block section
cannot do is let the model BUILD the plan incrementally while it explores, or let the
file outlive the transcript. `update_plan` writes here for both: one stable path per
session, inside the session's own `.reeve/` area — the same convention `tools/spill.py`
uses — so the file tools can read it back without an out-of-root approval prompt, and the
user can open it without the plan ever entering their project's tracked files.

The engine owns writing (the `update_plan` handler), NOT the permissioned file tools.
That is deliberate: PLAN mode's read-only guard is a boundary, deny-only by contract, and
carving a write exception for one path would weaken the floor the whole mode rests on.
An engine-owned write answers only to the plan's own lifecycle, so the model can keep a
plan current in exactly the mode whose job is planning.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

#: Session ids are validated at the API edge, but this module is also reachable from a
#: scripted caller — sanitize rather than trust, the same rule the spill store follows.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _field(root: Any, name: str, default: Any = None) -> Any:
    """One attribute of a root, whether it is a RootDir-like object or a plain dict."""
    if isinstance(root, dict):
        return root.get(name, default)
    return getattr(root, name, default)


def plan_path_for(
    roots: Iterable[Any], session_id: Optional[str]
) -> Optional[Path]:
    """The plan file path for a session, or None when there is nowhere to put it.

    Mirrors `SpillStore.under`: a scratch root wins (it is already the place for
    temporary session files), else the first writable root; no writable root means no
    plan file. `session_id` is required — a path with no session would let two sessions
    overwrite each other's plan.
    """
    if not session_id:
        return None
    writable = [r for r in roots or () if _field(r, "writable", False)]
    if not writable:
        return None
    scratch = next(
        (r for r in writable if _field(r, "label", "") == "scratch"), None
    )
    home = scratch or writable[0]
    name = _UNSAFE.sub("-", str(session_id)).strip("-") or "session"
    return Path(home.path) / ".reeve" / "plans" / f"{name}.md"


def write_plan(path: Path, content: str) -> None:
    """Write the plan whole, creating the directory and hiding it from git.

    One call, one file: a partial plan is worse than none because the next reader (the
    model after compaction, or the person opening it) would treat it as complete. Raises
    OSError — the `update_plan` handler reports it as a tool error, which is a normal
    result the model can react to, unlike a spill where failure must be invisible.
    """
    directory = path.parent
    existed = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not existed:
        try:
            (directory / ".gitignore").write_text("*\n", encoding="utf-8")
        except OSError:
            pass  # cosmetic only — a plan is worth more than a clean `git status`
    path.write_text(content, encoding="utf-8", errors="replace")


def read_plan(path: Optional[Path]) -> str:
    """The plan text, or "" when there is no file yet. Never raises: the API that reads
    this reports "no plan" rather than a 500 for a missing or unreadable file."""
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


__all__ = ["plan_path_for", "read_plan", "write_plan"]
