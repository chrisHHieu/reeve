"""Where a compaction cuts the history."""

from __future__ import annotations

from typing import Any, Optional

from harness.session.turns import is_user_turn

from .tokens import estimate_tokens


def turn_starts(messages: list[dict[str, Any]], *, start: int) -> tuple[list[int], list[int]]:
    """The candidate boundary indexes past `start`: user-message indexes (turn starts, preferred) and
    assistant indexes (iteration starts, which are legal heads of a suffix, since a `tool` message
    must never head the outbound view)."""
    users, assistants = [], []
    for index in range(start, len(messages)):
        if is_user_turn(messages[index]):
            users.append(index)
        elif messages[index].get("role") == "assistant":
            assistants.append(index)
    return users, assistants


def pick_boundary(messages: list[dict[str, Any]], *, keep_tokens: int) -> Optional[int]:
    """The canonical index where the verbatim tail begins: the earliest turn start whose suffix fits
    the keep budget. It prefers user-message boundaries and falls back to iteration (assistant)
    boundaries when the newest turn alone exceeds the budget, which a giant tool loop does. It returns
    None when there is nothing meaningful to summarize.

    `keep_tokens=0` therefore does not mean "keep nothing". Every non-empty suffix exceeds the budget,
    so the last fallback applies: the newest assistant step after the newest user turn, or the newest
    user turn itself when there is none. Zero keeps exactly the most recent step, which is what the
    config documentation says."""
    start = 1 if messages and messages[0].get("role") == "system" else 0
    users, assistants = turn_starts(messages, start=start)

    def earliest_fit(candidates: list[int]) -> Optional[int]:
        for index in candidates:  # earliest first, to keep as much verbatim as fits
            if estimate_tokens(messages[index:]) <= keep_tokens:
                return index
        return None

    boundary = earliest_fit(users)
    if boundary is None and users:
        # The newest user turn alone blows the budget, so the cut goes inside it at an iteration
        # boundary and keeps at least the most recent assistant step.
        inside = [index for index in assistants if index > users[-1]]
        boundary = earliest_fit(inside)
        if boundary is None:
            boundary = inside[-1] if inside else users[-1]
    if boundary is None:
        boundary = earliest_fit(assistants) or (assistants[-1] if assistants else None)
    # A boundary at or before the first real message summarizes nothing, so there is no cut.
    if boundary is None or boundary <= start:
        return None
    return boundary
