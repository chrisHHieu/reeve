"""A parent speaks to a subagent it started (`message_task`).

What a message does depends on where the child is, and each case answers with what will happen,
never only "sent":

- **In a turn** (`_live_child_turns`): the message joins that turn the way a person's message joins
  theirs (`queue_steering`), and the child reads it after the step it is on. A message that came
  after the turn's last read is taken when the turn ends (`take_steering`) and opens a turn of its
  own (`session_roots._run_child_turn`), so none is left behind in an engine nobody runs again.
- **Between two turns of a background job**: it is queued, and the job's completion writer runs it
  next (`_settle_background`). The job leaves the registry in the same locked step that finds its
  queue empty, so a message cannot land in a queue nobody drains again.
- **Finished**: the caller resumes it (`spawn_subagent` with its `task_id`) and waits for its answer.

The old path queued every message to a background child and the completion writer cleared the
queue AFTER it had delivered the result, which opened the parent's next turn: a message sent in
that turn was answered "sent" and then dropped (session 1967dcdb-7d2, 2026-10-12).

A child may also be called by the name its `task` call gave it. The name is the parent's own record
(the call's arguments, and the task id its result named), so it survives a restart without a column
of its own.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from harness.tools.task_message import DELIVERED, QUEUED

#: The `source` kind a message from the parent carries into the child's transcript. Display only:
#: `source` never reaches a provider, and a message with one is not the person speaking.
PARENT_MESSAGE = "parent_message"

_ID_IN_RESULT = re.compile(r'<task id="([^"]+)"|task_id: ([0-9A-Za-z_-]+)')


def parent_source(parent_id: str) -> dict[str, Any]:
    """The display-only sidecar of a message the parent sent its child."""
    return {"synthetic": PARENT_MESSAGE, "session_id": parent_id}


def named_children(messages: list[dict[str, Any]]) -> dict[str, str]:
    """`{name: child_id}` from a parent's transcript: each `task` call that gave a name, joined to
    the task id its result named. A call whose result is not in yet names nobody."""
    names: dict[str, str] = {}
    calls: dict[str, str] = {}
    for message in messages or []:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") != "task":
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        continue
                name = str((arguments or {}).get("name") or "").strip() if isinstance(arguments, dict) else ""
                if name and call.get("id"):
                    calls[str(call["id"])] = name
        elif message.get("role") == "tool":
            name = calls.pop(str(message.get("tool_call_id") or ""), "")
            content = message.get("content")
            found = _ID_IN_RESULT.search(content) if name and isinstance(content, str) else None
            if found:
                names[name] = found.group(1) or found.group(2)
    return names


def parent_messages(manager: Any, parent_id: str) -> list[dict[str, Any]]:
    """The parent's transcript: its live engine's, else the stored one."""
    engine = manager._engines.get(parent_id)
    if engine is not None:
        return list(getattr(engine, "messages", None) or [])
    record = manager.session_store.load(parent_id)
    return list(getattr(record, "messages", None) or [])


def name_taken(manager: Any, parent_id: str, name: str) -> str:
    """The child already called `name` in this session, or ""."""
    with manager._subagent_lock:
        known = manager._child_names.get(parent_id, {}).get(name, "")
    return known or named_children(parent_messages(manager, parent_id)).get(name, "")


def remember_name(manager: Any, parent_id: str, name: str, child_id: str) -> None:
    """Keep `name → child_id` for this process, so a second call in the same step cannot take
    the name before the first one's result is in the transcript."""
    with manager._subagent_lock:
        manager._child_names.setdefault(parent_id, {})[name] = child_id


def name_of(manager: Any, parent_id: str, child_id: str) -> str:
    """The name the parent gave `child_id`, or "" (for the "still running" line)."""
    with manager._subagent_lock:
        for name, known in manager._child_names.get(parent_id, {}).items():
            if known == child_id:
                return name
    return ""


def resolve_child(manager: Any, parent_id: str, to: str) -> tuple[str, str]:
    """`(child_id, "")` for a subagent this session started, named by its id or its name, else
    `("", why)`."""
    to = str(to or "").strip()
    if not to:
        return "", "say which subagent: its name or its task_id"
    child_id = to
    record = manager.session_store.load(to)
    if record is None or getattr(record, "kind", "") != "subagent":
        child_id = name_taken(manager, parent_id, to)
        record = manager.session_store.load(child_id) if child_id else None
    if record is None or getattr(record, "kind", "") != "subagent":
        return "", f'no subagent of this session is called "{to}": use the name or task_id its task call returned'
    if record.parent_session != parent_id:
        return "", f"{to} is not a subagent this session started"
    return child_id, ""


async def deliver_to_child(
    manager: Any,
    *,
    parent_id: str,
    child_id: str,
    prompt: str,
    description: str = "",
    on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
    parent_engine: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    """Give `prompt` to a child that is still at work, or return None when it is not (the caller
    then resumes it). See the module docstring for the two cases."""
    from harness.tools.task import render_output

    with manager._subagent_lock:
        engine = manager._engines.get(child_id) if child_id in manager._live_child_turns else None
        if engine is not None:
            engine.queue_steering(prompt, parent_source(parent_id))
            state, summary, text = "running", "Message delivered", DELIVERED
        elif child_id in manager._background_tasks:
            # The job's own definition runs it: the completion writer fills in the agent.
            manager._background_followups.setdefault(child_id, []).append(
                (description, prompt, None, on_metadata, parent_engine)
            )
            state, summary, text = "running", "Message queued", QUEUED
        else:
            return None
    if engine is not None:
        # The child's viewers see it land inside the turn, the way a person's own message does
        # (`steering_queued`); a reload draws it from the transcript.
        await manager.broadcast_session(
            child_id,
            {"type": "steering_queued", "data": {"text": prompt, "source": parent_source(parent_id)}},
        )
    return {
        "task_id": child_id,
        "status": state,
        "report": render_output(session_id=child_id, state=state, summary=summary, text=text),
    }


def effort_problem(effort: str, route: str, *, cli: bool) -> str:
    """Why a child on `route` cannot think at `effort`, or ""."""
    if cli:
        return "effort applies to an in-process subagent; a CLI agent sets its own"
    from harness.llm.providers.variants import levels_for

    levels = levels_for(route)
    if not levels:
        return f"{route or 'this model'} offers no thinking levels: leave effort out"
    if effort not in levels:
        return f'effort "{effort}" is not one {route} offers: {", ".join(levels)}'
    return ""
