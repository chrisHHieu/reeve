"""Which background subagents are still running — told to the parent every turn.

A background `task` returns at once ("running… you will be notified when it finishes") and
the model is told not to poll. Nothing afterwards said which children were still in flight,
so after a long stretch of other work the model could not tell "still running" from "lost".
Session b3a8491e-9db (2026-09-28): two explorers ran in the background; the backend one
reported, and when the owner asked "did both finish?" the model decided the frontend one
"never reported back" and spawned a second frontend explorer while the first was still
working. Both finished; the work was done twice.

The manager knows exactly which children are in flight (`_background_parents`, cleared by
the completion writer), so the parent's live context carries that list — the same channel
as the clock — and the model reads the answer instead of guessing it. No elapsed time on
purpose: the line changes only when the set does, so it is restated only then.
"""

from __future__ import annotations

from typing import Any


def background_context(manager: Any, parent_id: str) -> str:
    """The "still running" line for `parent_id`, or "" when none of its children is."""
    from .subagent_messages import name_of

    with manager._subagent_lock:
        running = sorted(
            (child_id, manager._background_labels.get(child_id, ""))
            for child_id, parent in manager._background_parents.items()
            if parent == parent_id
        )
    # Calls queued for a slot: not started, so not "running", and not lost.
    queued = [description for _task, description in list(getattr(manager, "_queued_spawns", {}).get(parent_id, ()))]
    waiting = (
        "Queued subagents, each starting by itself when a running one finishes (its result arrives "
        "as a message too; do not start the same work again):\n"
        + "\n".join(f"- {description}" for description in queued)
        if queued
        else ""
    )
    if not running:
        return waiting
    rows = []
    for child_id, label in running:
        # The name its `task` call gave it, when it has one: what `message_task` takes.
        name = name_of(manager, parent_id, child_id)
        rows.append(f"- {child_id}" + (f" ({name})" if name else "") + (f" — {label}" if label else ""))
    return (
        "Background subagents still running (each one's result arrives as a message when it "
        "finishes; until then it is working, not lost — do not start the same work again; "
        "to tell one something, call message_task with its name or task_id):\n" + "\n".join(rows)
    ) + ("\n" + waiting if waiting else "")
