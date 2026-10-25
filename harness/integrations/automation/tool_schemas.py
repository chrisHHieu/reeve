"""The schemas the model sees for the four scheduling tools."""

from __future__ import annotations

from harness.tools.declare import function_schema as _function

_PERMISSION_ITEM = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "Exact tool name, e.g. 'send_message'."},
        "target": {"type": "string", "description": "The exact target argument value the rule binds to."},
        "access": {
            "type": "string",
            "enum": ["read", "write"],
            "description": "'write' proposes a standing grant; 'read' is disclosure.",
        },
    },
    "required": ["tool", "target", "access"],
}

CREATE = _function(
    "create_scheduled_task",
    "Create a scheduled automation that re-runs `instructions` on a schedule. Convert "
    "the user's natural-language timing into a cron expression yourself (e.g. "
    "'every day at 7:10pm' → '10 19 * * *'), or pass a one-time `fire_at` ISO datetime. "
    "The user confirms before it is created.",
    {
        "title": {"type": "string", "description": "Short label, e.g. 'Daily news briefing'."},
        "instructions": {
            "type": "string",
            "description": (
                "What to do on each run, written as a direct command to execute "
                "immediately (e.g. 'Prepare a market analysis report covering …'). Do "
                "NOT restate the schedule or timing here — timing belongs in cron/"
                "fire_at; this text is handed verbatim to the agent every run."
            ),
        },
        "cron": {"type": "string", "description": "5-field cron, e.g. '10 19 * * *'. Omit for one-time."},
        "fire_at": {"type": "string", "description": "ISO datetime for a one-time run. Omit for recurring."},
        "timezone": {
            "type": "string",
            "description": "IANA tz, e.g. 'America/New_York'. Defaults to the machine's local time — pass it only to override.",
        },
        "permissions": {
            "type": "array",
            "description": (
                "What this automation will touch, surfaced on the creation consent "
                "card. List every external read and write the instructions imply. "
                "Reads (access:'read') are disclosure only. Writes (access:'write') "
                "become standing grants IF the user approves: the automation may then "
                "call that exact tool against that exact target without asking each "
                "run. Targets must be exact (a channel address like 'slack:T…/C…', a "
                "recipient) — no wildcards. Omit writes whose target you don't know "
                "yet; the run will ask instead."
            ),
            "items": _PERMISSION_ITEM,
        },
    },
    ["title", "instructions"],
)

UPDATE = _function(
    "update_scheduled_task",
    "Enable/disable or edit a scheduled task (its instructions, cron, or title).",
    {
        "id": {"type": "string"},
        "enabled": {"type": "boolean"},
        "instructions": {"type": "string"},
        "cron": {"type": "string"},
        "title": {"type": "string"},
    },
    ["id"],
)

DELETE = _function(
    "delete_scheduled_task",
    "Delete a scheduled task and its run history.",
    {"id": {"type": "string"}},
    ["id"],
)

LIST = _function(
    "list_scheduled_tasks",
    "List the user's scheduled tasks (title, schedule, next run, status).",
    {},
)
