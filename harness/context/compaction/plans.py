"""The plans a compacted block carries: the task list as it stands, and the plan the person approved."""

from __future__ import annotations

from typing import Any, Optional

# The statuses `todo_write` records, and how each reads in the block (harness/tools/todo.py owns the
# vocabulary). They are spelled out here and not imported, because that module pulls the tool
# runtime in and this one is pure by design. The tool is the only writer, so the two cannot drift
# without a test noticing.
PLAN_MARKS = {"done": "[x]", "in_progress": "[>]", "pending": "[ ]"}
# A plan past this length is a model narrating and not planning. Like every other bound here it keeps
# the head and a count of what went, since a list silently cut is one the model reasons about as
# though it were whole.
PLAN_MAX_ITEMS = 40
PLAN_ITEM_CLIP = 200

# How much of an approved plan the block carries. It is larger than one todo item because a plan is
# prose the session carries out step by step, and smaller than the block's own share of the window.
# Past this the plan is a document, and the block names the cut and does not let the plan swallow the
# window.
APPROVED_PLAN_CLIP = 6_000


def clip_approved_plan(plan: Optional[str]) -> str:
    """An approved plan within the block's budget, with a note where it was cut."""
    if not plan:
        return ""
    if len(plan) <= APPROVED_PLAN_CLIP:
        return plan
    return (
        plan[: APPROVED_PLAN_CLIP - 1]
        + "\n…[plan truncated for the compacted block — the full text is in the "
        "user-approved card above the compaction boundary]"
    )


def normalize_plan(raw: Any) -> list[dict[str, str]]:
    """A `todo_write` list reduced to the two fields the block renders.

    It is tolerant on the way in, for the reason the tool is (tools/todo.py): a plan is a display, so
    a malformed entry costs that entry and not the block it lives in."""
    out: list[dict[str, str]] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            content = str(entry.get("content", "")).strip()
            status = str(entry.get("status", "pending"))
        else:
            content, status = str(entry).strip(), "pending"
        if not content:
            continue
        out.append({"content": content, "status": status if status in PLAN_MARKS else "pending"})
    return out


def render_standing_plan(plan: list[dict[str, str]]) -> str:
    """The plan section of the compacted block: the list, and an instruction to issue it again.

    The instruction is the half that matters. A model handed the list knows where the work stands
    and carries on correctly. The panel does not, because the panel is fed by `todo_write` calls and
    this block is text and not a call. Without the instruction the agent finishes the job while the
    task list the person is watching stays frozen at whatever it last saw, which is exactly what
    compacting mid-turn did before this section existed."""
    if not plan:
        return ""
    shown = plan[:PLAN_MAX_ITEMS]
    lines = ["## Standing plan (your task list as it stands)"]
    for item in shown:
        content = item["content"]
        if len(content) > PLAN_ITEM_CLIP:
            content = content[: PLAN_ITEM_CLIP - 1] + "…"
        lines.append(f"{PLAN_MARKS[item['status']]} {content}")
    if len(plan) > len(shown):
        lines.append(f"({len(plan) - len(shown)} further items omitted)")
    lines.append(
        "Re-issue this list with `todo_write` before your next action, with the statuses "
        "moved to where the work actually stands. This block is text, not a call: until "
        "you make that call the task list the user is watching stays frozen."
    )
    return "\n".join(lines)
