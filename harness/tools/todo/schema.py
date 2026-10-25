"""`todo_write`: its name and the schema the model is shown."""

from __future__ import annotations

# The tool's name in one place. Three readers key off it: the engine, which records a `todo/write`
# event when a call succeeds, compaction, which carries the standing plan through a summarized span,
# and the GUI's plan fold. A name spelled out in four files is one rename away from a panel that
# silently stops updating.
TODO_WRITE = "todo_write"

STATUSES = {"pending", "in_progress", "done"}

# Written out in full, because an array of objects cannot be generated reliably and providers reject
# a bare `list` annotation. It is attached as `__tool_schema__`.
#
# The parameter is `todos` and not `items`. A top-level argument named "items" shadows minijinja's
# `.items()` map method in at least one hosted chat template (Together's GLM-5.2, 2026-07-21: "object
# is not callable"), and that 400s every request that replays the call. Any key that is not a
# minijinja map method is safe. Never rename it back.
TODO_SCHEMA = {
    "type": "function",
    "function": {
        "name": TODO_WRITE,
        "description": (
            "Maintain the session's task plan, always replacing the full list: exactly "
            "one item in_progress, finished items done. Write it when multi-step work "
            "starts and update it as items finish — each time in the SAME step as the "
            "next working tool call (call both at once), never as a step of its own."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    },
}
