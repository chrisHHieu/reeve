"""What a person sees of the plan (see `tools/contract.py`).

The plan is read from the arguments and not from the result. The list the model typed is the list to
show, and it is the same on the call and on the result. A surface that draws the plan strip reads
`args.todos` directly for the same reason: one shape, and one reader per surface, with no second
vocabulary between them.

The tally of statuses is folded here and not left to each surface. By the time a model is done,
"done" has two spellings (the alias is normalized in `tool.py`), and a surface counting raw strings
would report a finished plan as unfinished.
"""

from __future__ import annotations

import json
from typing import Any

from .. import cards
from ..contract import ToolContract, ToolPresentation, clip


def _entries(arguments: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    for source in (
        value.get("todos") if isinstance(value, dict) else None,
        arguments.get("todos"),
        arguments.get("items"),
    ):
        if isinstance(source, list):
            return [entry for entry in source if isinstance(entry, dict)]
    return []


def _present(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    entries = _entries(arguments, value)
    total = len(entries)
    finished = sum(1 for entry in entries if entry.get("status") in ("done", "completed"))
    current = next(
        (str(entry.get("content") or "") for entry in entries if entry.get("status") == "in_progress"),
        "",
    )
    summary = f"Updated the plan — {total} item{'' if total == 1 else 's'}"
    if total:
        summary += f", {finished} done"
    return ToolPresentation(
        summary=summary,
        detail=clip(current, 120),
        kind="other",
        card=cards.generic_call("Update todo list", kind="other", raw_input=entries),
    )


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    return _present(arguments, {})


def _present_result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    return _present(arguments, value)


def _render(arguments: dict[str, Any], value: Any) -> str:
    """The plan as a checklist, in place of the JSON list of `{content, status}` rows."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    marks = {"done": "x", "completed": "x", "in_progress": "~"}
    rows = [
        f"[{marks.get(str(entry.get('status')), ' ')}] {entry.get('content', '')}"
        for entry in _entries(arguments, value)
    ]
    return "\n".join(rows) if rows else "(empty list)"


TODO_CONTRACT = ToolContract(
    render=_render,
    present_call=_present_call,
    present_result=_present_result,
)
