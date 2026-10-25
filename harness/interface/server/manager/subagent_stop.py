"""A parent model stops its own background subagent (`task(task_id=…, stop=true)`).

Until now only a person could stop a child: the Stop button, or the background clock. A task the
model handed out and no longer needs (the answer arrived another way, the brief was wrong, a second
child already covers it) kept running and spending until it ended on its own.

The stop pulls the same interrupt tree the Stop button does. A child stopped that way settles
`interrupted`, and the completion writer drops an interrupted result (a person's Stop must not
reopen the parent), so the stop's own answer is the one report the parent gets: the child's work up
to that moment, never lost.
"""

from __future__ import annotations

import time
from typing import Any

#: How long the stop waits for the child's turn to settle, so its last words are on disk.
SETTLE_SECONDS = 15.0


def stop_subagent(manager: Any, parent_id: str, child_id: str) -> dict[str, Any]:
    """Stop `child_id`, a background child of `parent_id`, and report what it had done."""
    from harness.tools.task import render_output

    with manager._subagent_lock:
        owner = manager._background_parents.get(child_id)
        running = child_id in manager._background_tasks
        if running and owner == parent_id:
            # Queued follow-ups would start new turns after the interrupt: they go first.
            manager._background_followups.pop(child_id, None)
    if not running:
        return {
            "error": (
                f"{child_id} is not running in the background: its result has already come "
                "back, or it ran in the foreground and is done."
            )
        }
    if owner != parent_id:
        return {"error": f"{child_id} is not a task this session started."}
    stopped: list[str] = []
    manager._cancel_session_tree(child_id, stopped)
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        with manager._subagent_lock:
            if child_id not in manager._background_tasks:
                break
        time.sleep(0.05)
    text = _last_words(manager, child_id)
    return {
        "task_id": child_id,
        "status": "stopped",
        "report": render_output(
            session_id=child_id,
            state="stopped",
            summary="Stopped by this session",
            text=text or "(it had not written anything yet)",
        ),
    }


def _last_words(manager: Any, child_id: str) -> str:
    """The child's last assistant text: what it had found when it was stopped."""
    record = manager.session_store.load(child_id)
    for message in reversed(getattr(record, "messages", None) or []):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""
