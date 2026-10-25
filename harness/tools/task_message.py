"""The `message_task` tool: the parent talks to a subagent it started.

`task` hands out work. Everything said to a subagent afterwards goes through this tool, with the
subagent named by the `name` its `task` call gave it or by its task id:

- **at work**: the message joins the turn it is in, read after the step it is on, and the call
  returns at once; its one result still comes as promised;
- **finished**: the message continues it, with everything it has read, and the call returns its
  answer;
- **`stop=true`**: a background subagent whose work is no longer needed is stopped, and what it had
  found comes back (which put this on `task` until a tool of its own existed).

The words the model reads (the description, `DELIVERED`, `QUEUED`) and the name rule live here, in
the tools layer; the manager (`manager/subagent_messages.py`) decides which case applies.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .contract import ToolContract, ToolPresentation, attach_contract, clip

#: A name a `task` call may give its child: short, lowercase, and never mistaken for an id.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

DESCRIPTION = """Talk to a subagent this session started, named by the name its task call gave it or by its task_id.

- While it is working: the message joins its work, and it reads it after the step it is on. This call returns at once and nothing else comes back from it; the subagent's result still arrives once, when it finishes.
- After it finished: the message continues it, with everything it has already read, and this call returns its answer.
- stop=true instead of a message: stop a background subagent whose work is no longer needed, and get back what it had done so far.

It sees only what you send, never this conversation: write the message the way you wrote its brief."""

DELIVERED = "\n".join(
    [
        "Delivered: the subagent reads this after the step it is on now, as part of the work it is doing.",
        "Nothing else comes back from this call. Its result still arrives once, when it finishes: as a message if it runs in the background, or as the answer of the call that is waiting for it.",
        "DO NOT sleep, poll for progress, or do the work you just described to it.",
    ]
)

QUEUED = "\n".join(
    [
        "The subagent had just finished a turn of its job, so this message opens its next turn right away.",
        "Its result arrives once, as a message, when that turn ends. DO NOT sleep, poll for progress, or do that work yourself.",
    ]
)


def name_problem(name: str) -> str:
    """Why `name` cannot name a subagent, or ""."""
    if NAME_PATTERN.match(name):
        return ""
    return (
        f'name "{name}" is not usable: start with a letter, then lowercase letters, digits or '
        'dashes, at most 32 characters (e.g. "survey", "tests-2")'
    )


def message_task_tool(
    *,
    spawner: Callable[..., dict],
    session_id: str,
    metadata_tap: Callable[[], Optional[Callable[[dict], None]]],
    settle: Callable[[dict], dict],
) -> Callable[..., dict]:
    """Build the session's `message_task`. `spawner` is the manager's sync door, `metadata_tap`
    binds the parent row's live frames to this call (as `task` does), and `settle` gives a resumed
    child's answer the same envelope a `task` call's has."""

    def message_task(to: str, message: str = "", stop: bool = False) -> dict:
        """Talk to a subagent this session started.

        Args:
            to (str): The subagent: the name its task call gave it, or its task_id.
            message (str): What to tell it. Omit with stop=true.
            stop (bool): Stop that background subagent instead, and get back what it had done.
        """
        text = str(message or "").strip()
        if stop and text:
            return {"error": "send a message or stop it, not both"}
        if stop:
            out = spawner(parent_id=session_id, to=to, stop=True)
            if out.get("error"):
                return {"error": str(out["error"])}
            return {"task_id": out.get("task_id"), "status": "stopped", "report": out.get("report")}
        if not text:
            return {"error": "message is empty: say what it should know or do, or pass stop=true"}
        return settle(
            spawner(
                parent_id=session_id,
                to=to,
                description="",
                prompt=text,
                agent_name="",
                on_metadata=metadata_tap(),
            )
        )

    message_task.__doc__ = DESCRIPTION
    message_task.__tool_schema__ = _schema()
    # A message starts no new subagent, so it is read-shaped for the gate the way `task` is.
    message_task.__tool_policy__ = lambda arguments: {
        "category": "search",
        "risk_level": "low",
        "capabilities": ["search"],
        "requires_approval": False,
    }
    attach_contract(
        message_task,
        ToolContract(
            render=_render,
            present_call=_present_call,
            present_result=_present_result,
            # A finished child is resumed and waited for, like a `task` call.
            timeout_ms=(15 * 60 + 60) * 1000,
        ),
    )
    return message_task


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "message_task",
            "description": DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "The subagent: the name its task call gave it, or its task_id",
                    },
                    "message": {
                        "type": "string",
                        "description": "What to tell it, standing on its own. Omit with stop=true",
                    },
                    "stop": {
                        "type": "boolean",
                        "description": (
                            "Stop that background subagent instead of messaging it, when its work "
                            "is no longer needed (the answer came another way, or the brief was "
                            "wrong), and get back what it had done so far"
                        ),
                    },
                },
                "required": ["to"],
            },
        },
    }


def _render(arguments: dict[str, Any], value: Any) -> str:
    if isinstance(value, dict):
        if "error" in value:
            return str(value["error"])
        if isinstance(value.get("report"), str):
            return value["report"]
    return str(value)


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    to = str((arguments or {}).get("to") or "subagent")
    if (arguments or {}).get("stop"):
        return ToolPresentation(summary=f"Stop {to}", kind="other")
    text = " ".join(str((arguments or {}).get("message") or "").split())
    return ToolPresentation(summary=f"Message {to}" + (f" · {clip(text, 80)}" if text else ""), kind="other")


def _present_result(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    to = str((arguments or {}).get("to") or "subagent")
    if isinstance(value, dict) and "error" in value:
        return ToolPresentation(summary=f"Message to {to} failed", detail=clip(str(value["error"]), 300), kind="other")
    status = str((value or {}).get("status") or "") if isinstance(value, dict) else ""
    report = str((value or {}).get("report") or "") if isinstance(value, dict) else ""
    if status == "stopped":
        summary = f"Stopped {to}"
    elif status == "running":
        summary = f"Sent to {to} · it reads it next" if "Delivered" in report else f"Sent to {to} · it starts on it next"
    else:
        summary = f"{to} answered"
    return ToolPresentation(summary=summary, kind="other")
