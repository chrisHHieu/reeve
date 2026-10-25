"""The todo tool: a structured task list the agent keeps and the UI draws.

It does a lot for how organised interactive work feels. It is low risk and auto-approved. The list
lives in a `TodoList` that a surface can read, and `todo_write` replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import aisuite as ai

from ..contract import attach_contract
from .present import TODO_CONTRACT
from .schema import STATUSES, TODO_SCHEMA, TODO_WRITE

__all__ = ["TODO_CONTRACT", "TODO_WRITE", "TodoList", "todo_tools"]


@dataclass
class TodoList:
    items: list[dict] = field(default_factory=list)


def _normalized(entries: list) -> list[dict]:
    """Each entry as `{content, status}`. A bare value becomes a pending item, `completed` is read as
    `done` (a common alias), and any status we do not know becomes `pending`."""
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            cleaned.append({"content": str(entry), "status": "pending"})
            continue
        status = entry.get("status", "pending")
        if status == "completed":
            status = "done"
        cleaned.append(
            {"content": str(entry.get("content", "")), "status": status if status in STATUSES else "pending"}
        )
    return cleaned


def todo_tools(todo: TodoList) -> list:
    def todo_write(todos: list = None, items: list = None) -> dict:
        """Replace the task list. Each todo is an object with `content` and a `status`
        of pending, in_progress, or done."""
        # `items` is still accepted, for models that free-style the old name and for queued replays.
        cleaned = _normalized((todos if todos is not None else items) or [])
        active = [entry["content"] for entry in cleaned if entry["status"] == "in_progress"]
        if len(active) > 1:
            # One item in progress at a time: several at once is how an agent overreaches and
            # under-finishes. The rule was only prose in the description. Now the list is refused
            # and kept as it was, until exactly one item is active.
            return {
                "error": (
                    f"exactly one item may be in_progress; {len(active)} are: "
                    + "; ".join(repr(content[:60]) for content in active)
                    + " — finish one first, mark the rest pending, and write the list again"
                )
            }
        todo.items = cleaned
        return {"count": len(cleaned), "todos": cleaned}

    tool = ai.tool(
        todo_write,
        metadata=ai.ToolMetadata(category="planning", risk_level="low", capabilities=["todo"]),
    )
    tool.__tool_schema__ = TODO_SCHEMA
    attach_contract(tool, TODO_CONTRACT)
    return [tool]
