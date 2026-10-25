"""The words a goal round opens and closes with (split from `goals.py`, size ceiling).

`round_prompt` is the one retained instruction each round carries; `wrapup_instruction`
is what a terminal `done`/`blocked` returns so the run ends on a summary the person
reads. Both are pure: a goal in, text out.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .goal_ledger import progress_lines
from .goal_proof import proof_instruction

if TYPE_CHECKING:
    from .goals import Goal


def round_prompt(goal: "Goal", round_no: int) -> str:
    """The one retained instruction a round opens with.

    Self-contained by design (objective, round, authority, exits): a round can be the
    first thing a resumed session reads, and an instruction that assumes earlier context
    is an instruction that assumes wrong. The harness's own ledger of the latest rounds
    rides along (`progress_lines`), so a round that follows a compaction still
    knows what moved and what did not without re-reading the work. The FINAL permitted
    round carries one extra paragraph (the wrap-up instruction): the model is told to close with a user-facing summary
    instead of ending on a bare status line, because the harness stops the goal right
    after it.
    """
    lines = [
        "<goal_round>",
        f"Objective: {json.dumps(goal.objective)}",
        f"Round {round_no} of {goal.max_rounds}.",
        (
            "Continue toward the objective. The workspace, the tool results already in "
            "this session and its durable state are the only authority — do not re-ask "
            "what this session has already established. When the objective is complete, "
            'call goal(action="done", reason="..."). If only the user can unblock you, '
            'call goal(action="blocked", reason="...") instead of stalling. Otherwise '
            "take the next concrete step now."
        ),
    ]
    lines.append(proof_instruction(goal))
    lines.extend(progress_lines(goal))
    if round_no >= goal.max_rounds:
        lines.append(
            "This is the FINAL permitted round. If the objective is complete, call "
            'goal(action="done", reason="...") now. If it is not, leave the goal active, '
            "take the most valuable step you still can, and close with a short "
            "user-facing summary — what is done, what remains, and what would unblock "
            "it. Do not end the turn on a bare status line."
        )
    lines.append("</goal_round>")
    return "\n".join(lines)


def wrapup_instruction(goal: "Goal", *, blocked_reason: str = "") -> str:
    """The closing-message instruction a terminal `done`/`blocked` returns (adapted to this harness's tool-result channel: the model
    reads it as the call's own result in the same turn, so it writes the summary that
    ends the autonomous run instead of the harness ending on a bare notice)."""
    grounding = (
        "Report only what earlier rounds and tool results in this session actually "
        "establish; when a detail is not in the session, say so instead of inventing "
        "it. "
    )
    heading = f"Objective: {json.dumps(goal.objective)}"
    if blocked_reason:
        return (
            "<goal_blocked>\n"
            f"{heading}\n"
            f"Blocked: {json.dumps(blocked_reason)}\n"
            "The goal is marked blocked and this autonomous run is ending. Write the "
            "closing message to the user now: state what has been completed so far, "
            "describe the concrete blocking condition and what you tried, and say "
            "exactly what you need from the user to continue. "
            f"{grounding}"
            "Address the user directly. Do not call more tools in this round.\n"
            "</goal_blocked>"
        )
    return (
        "<goal_complete>\n"
        f"{heading}\n"
        "The goal is marked complete and this autonomous run is ending. Write the "
        "closing message to the user now: state the outcome, summarize what was done "
        "and how it was verified, and point to the concrete results (files, commits, or "
        "other artifacts). "
        f"{grounding}"
        "Note anything the user should review or do next. Address the user directly. "
        "Do not call more tools in this round.\n"
        "</goal_complete>"
    )


__all__ = ["round_prompt", "wrapup_instruction"]
