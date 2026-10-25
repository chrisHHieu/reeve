"""What a live answer frame resolves its parked prompt with.

Each kind of prompt carries its reply to the inbox item as one string, so the same item can be
settled from the socket, over REST or from a bound channel.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional


def _approval(message: dict) -> Any:
    decision = message.get("decision", "deny")
    if decision != "modify":
        return decision
    # The edited arguments are INPUT to the engine's decision, not a permission: the edited
    # call is validated and checked from scratch before anything runs.
    return json.dumps({"decision": "modify", "arguments": message.get("arguments") or {}})


def _tool_install(message: dict) -> str:
    return json.dumps({"approved": bool(message.get("approved"))})


def _plan(message: dict) -> str:
    return json.dumps(
        {
            "approved": bool(message.get("approved")),
            "mode": message.get("mode", "interactive"),
            "feedback": message.get("feedback", ""),
        }
    )


_BUILDERS: dict[str, Callable[[dict], Any]] = {
    "approval": _approval,
    "tool_response": _tool_install,
    "plan_response": _plan,
    "question_response": lambda message: str(message.get("answer", "")),
}


def reply_for(kind: str, message: dict) -> Optional[Any]:
    """The resolution string for an answer frame, or None when `kind` is not one."""
    build = _BUILDERS.get(kind)
    return build(message) if build else None
