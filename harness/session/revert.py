"""Message-level revert — a projection over the canonical transcript.

`/undo` does not delete anything. It writes a marker on the session record
(`SessionRecord.revert`) naming a boundary message and the length of the transcript at
the moment the marker was staged. The outbound assembly then sends
`messages[:boundary]` plus whatever was appended after staging — the model's view is
truncated, the stored transcript is not. `/redo` moves the boundary forward through
later user messages, or clears it.

Why a span and not a single cut: A destructive cleanup would DELETE everything
from the boundary on when the next prompt arrives, so the model sees the prefix plus
the new message. We cannot delete (the transcript is the record), so the marker keeps
`staged_at` — the append cursor — and the view is prefix + post-revert tail. That is
the same model view without the deletion.

The marker shape (all fields ints/floats, JSON-safe):

    {"boundary": 7, "staged_at": 12, "ts": 1758000000.0}

`boundary` is a canonical index whose message must be a user turn; `staged_at` is
`len(messages)` when the marker was staged. Everything in `[boundary, staged_at)` is
hidden from the model; `[staged_at, …)` is the branch that came after the undo.
"""

from __future__ import annotations

from typing import Any, Optional

from .turns import is_user_turn


def marker_key(marker: Optional[dict[str, Any]]) -> Optional[tuple[int, int]]:
    """What a marker hides, as `(boundary, staged_at)`: two markers with the same key give
    the same view. None when there is no usable marker."""
    boundary = marker_boundary(marker)
    if boundary is None:
        return None
    staged = marker.get("staged_at") if isinstance(marker, dict) else None
    return (boundary, int(staged) if isinstance(staged, int) else boundary)


def last_logged(rows: Any) -> Optional[tuple[int, int]]:
    """The marker a session log last recorded (`revert/set`), as `marker_key` gives it: a
    row that cleared the marker carries no boundary, and reads as none."""
    for row in reversed(list(rows or [])):
        if isinstance(row, dict) and row.get("type") == "revert/set":
            return marker_key(row)
    return None


def marker_boundary(marker: Optional[dict[str, Any]]) -> Optional[int]:
    """The canonical boundary index, or None when there is no usable marker."""
    if not isinstance(marker, dict):
        return None
    raw = marker.get("boundary")
    if not isinstance(raw, int) or raw < 0:
        return None
    return raw


def hidden_count(marker: Optional[dict[str, Any]]) -> int:
    """How many canonical messages the marker hides from the model."""
    boundary = marker_boundary(marker)
    if boundary is None:
        return 0
    staged_at = marker.get("staged_at")
    if not isinstance(staged_at, int) or staged_at < boundary:
        return 0
    return staged_at - boundary


def revert_view(
    messages: list[dict[str, Any]], marker: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The model-facing view of `messages` under a revert marker.

    `messages[:boundary]` plus the post-revert tail. A tail that does not open with a
    user turn is dropped entirely: the window between staging and the next send can
    catch an interrupted turn's trailing assistant/tool debris, and a branch that opens
    mid-step is not a request any provider accepts (the same orphan rule
    `_fork_safe_prefix` enforces).
    """
    boundary = marker_boundary(marker)
    if boundary is None or boundary >= len(messages):
        return list(messages)
    prefix = messages[:boundary]
    staged_at = marker.get("staged_at")
    if not isinstance(staged_at, int) or staged_at >= len(messages):
        return prefix
    tail = messages[staged_at:]
    for index, message in enumerate(tail):
        if is_user_turn(message):
            return prefix + tail[index:]
    return prefix


def compaction_fits(state: Any, marker: Optional[dict[str, Any]]) -> bool:
    """Whether a compaction projection can ride a revert-truncated view unchanged.

    The state's indexes address the CANONICAL list. They stay valid only while the
    whole projection lies inside the kept prefix: the summarized span
    (`boundary_index`) must not contain hidden messages (the block would leak them as a
    summary) and the pruned range (`pruned_upto`) must not reach past the boundary (the
    view's indexes shift there). A state that fails this is dropped for the duration of
    the revert — the truncated canonical history is sent instead. `/redo` (clear)
    restores it.
    """
    boundary = marker_boundary(marker)
    if boundary is None:
        return True
    if getattr(state, "pruned_upto", 0) > boundary:
        return False
    compaction_boundary = int(getattr(state, "boundary_index", 0) or 0)
    return compaction_boundary == 0 or compaction_boundary <= boundary


def visible_user_indexes(
    messages: list[dict[str, Any]], marker: Optional[dict[str, Any]]
) -> list[int]:
    """Canonical indexes of the user turns the MODEL still sees under the marker."""
    boundary = marker_boundary(marker)
    return [
        index
        for index, message in enumerate(messages)
        if is_user_turn(message) and (boundary is None or index < boundary)
    ]
