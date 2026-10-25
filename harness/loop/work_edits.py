"""Which edits change the work a check judged (split from `verification.py`).

A passing check stays proof until something it ran over changes. Every edit tool call
used to count, including the model saving its own memory notes — which voided a goal's
verified PASS in session b3a8491e-9db. `is_work_edit` is the one rule: an edit counts
unless every path it writes lies under Reeve's state directory (memory, project notes).
An edit whose paths cannot be read counts, so the rule only ever narrows what is ignored.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional, Sequence

#: Tools whose call invalidates an earlier check (the same list the task ruleset uses).
EDIT_TOOLS = frozenset(
    {"apply_patch", "apply_unified_diff", "replace_in_file", "write_file", "edit_notebook"}
)


def _edited_paths(name: str, arguments: Any) -> Optional[list[str]]:
    """The paths an edit call writes, or None when they cannot be read from its arguments."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return None
    if not isinstance(arguments, Mapping):
        return None
    path = arguments.get("path") or arguments.get("file_path")
    if path:
        return [str(path)]
    patch = str(arguments.get("patch") or arguments.get("input") or arguments.get("diff") or "")
    found = [
        line.split(":", 1)[1].strip()
        for line in patch.splitlines()
        if line.startswith(("*** Update File:", "*** Add File:", "*** Delete File:"))
    ] + [
        line[4:].strip().split("\t")[0].removeprefix("b/")
        for line in patch.splitlines()
        if line.startswith("+++ ") and "/dev/null" not in line
    ]
    return found or None


def _state_root() -> Optional[str]:
    try:
        from harness.safety.secrets import state_dir

        return os.path.normcase(os.path.realpath(str(state_dir())))
    except Exception:
        return None


def is_work_edit(
    name: str, arguments: Any, command: str = "", exempt: Sequence[str] = ()
) -> bool:
    """Whether an edit call changes the work a check judges.

    Reeve's own notes are not the work: a memory file or a project note written after a
    passing check changes nothing the check ran over. Session b3a8491e-9db (2026-09-28):
    a goal's verified PASS was voided by the model saving three memory notes, and six more
    rounds (about 8M tokens) went to verifying the same work again. Only edits whose every
    path lies under the state directory are exempt; an edit whose paths cannot be read
    counts, so the rule can only narrow what is ignored, never guess.

    `exempt` adds the session's own scratch folder when the work happens somewhere else
    (a project folder): a script or a handoff note written there changes nothing the
    project's checks ran over (session b3a8491e-9db, 2026-10-01: three scratch files kept
    a passing gate "stale"). Never for a session that WORKS in its scratch folder (the
    caller passes nothing then), and never for a file the check itself names: `command`
    is the check, and `python scratch/guard.py` is judged by guard.py."""
    if name not in EDIT_TOOLS:
        return False
    paths = _edited_paths(name, arguments)
    root = _state_root()
    if not paths or root is None:
        return True
    aside = [os.path.normcase(os.path.realpath(os.path.expanduser(p))) for p in exempt if p]
    named = command.lower()
    for raw in paths:
        full = os.path.normcase(os.path.realpath(os.path.expanduser(raw)))
        if full == root or full.startswith(root + os.sep):
            continue
        if any(full.startswith(base + os.sep) for base in aside) and os.path.basename(raw).lower() not in named:
            continue
        return True
    return False


def work_edits_after(
    ordered: Sequence[Mapping[str, Any]], index: int, command: str = "", exempt: Sequence[str] = ()
) -> int:
    """Edit calls after transcript position `index` that change the work (`is_work_edit`)."""
    edits = 0
    for message in ordered[index + 1 :]:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if is_work_edit(str(function.get("name") or ""), function.get("arguments"), command, exempt):
                edits += 1
    return edits


def work_edit_paths(
    ordered: Sequence[Mapping[str, Any]], start: int, end: int, command: str = "", exempt: Sequence[str] = ()
) -> tuple[int, list[str], dict[str, str]]:
    """The work edits between two transcript positions (after `start`, before `end`): how
    many calls, the paths they name (first written first), and for each path the id of the
    call that wrote it first, whose before-image (`tools/undo.py`) is the file as it was
    before these edits. A call whose paths cannot be read is counted and names nothing."""
    edits = 0
    paths: list[str] = []
    first: dict[str, str] = {}
    for message in ordered[start + 1 : end]:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if not is_work_edit(name, function.get("arguments"), command, exempt):
                continue
            edits += 1
            for path in _edited_paths(name, function.get("arguments")) or []:
                if path not in paths:
                    paths.append(path)
                    if call.get("id"):
                        first[path] = str(call["id"])
    return edits, paths, first


__all__ = ["EDIT_TOOLS", "is_work_edit", "work_edit_paths", "work_edits_after"]
