"""The three shell tools as the model sees them. The wording is model-facing, so change it only with
evidence from a run."""

from __future__ import annotations

import copy
from typing import Any

from ..shell_kinds import Shell
from ..shell_note import WINDOWS_NOTE, shell_note
from .limits import DEFAULT_TIMEOUT, MAX_TIMEOUT

RUN_SHELL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "Run a shell command. Each call runs in its own process: the working "
            "DIRECTORY persists across calls (cd once, then run bare commands), but "
            "environment variables a command sets do NOT — set them again in the next "
            "command that needs them. stdin is closed, so nothing can answer a prompt: "
            "run tools non-interactively (-y / --yes / --non-interactive). The result "
            "carries exit_code and output; a NON-ZERO exit is data, not a crash — "
            "check it before moving on. Output longer than the limit keeps the END "
            "(where a command's verdict or summary usually is); a spilled full output "
            "names its file path. " + WINDOWS_NOTE + "Set "
            "run_in_background for long-running processes (servers, watchers, large "
            "exports); that argument says how its result reaches you."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to run.",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Short human-readable summary of what the command does (e.g. "
                        "'Install dependencies'), shown in approval prompts and logs."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        f"Max seconds to WAIT (default {int(DEFAULT_TIMEOUT)}, "
                        f"max {int(MAX_TIMEOUT)}). On expiry the command is NOT stopped: "
                        "it keeps running in the background, you are told when it "
                        "finishes, and the result names the task_id (shell_task_output "
                        "reads output early; shell_task_kill stops it). Ignored for "
                        "background tasks."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Run detached and return a task_id immediately instead of waiting. "
                        "YOU WILL BE TOLD WHEN IT FINISHES: the result arrives as a "
                        "follow-up message, so do other work or end your reply — NEVER poll "
                        "a background job (no sleep/ps/pgrep/top loops; each poll re-sends "
                        "the whole conversation). shell_task_output reads output EARLY if "
                        "you need it before then; shell_task_kill stops it. Use for "
                        "servers, watchers, and very long builds."
                    ),
                },
                "notify_when": {
                    "type": "string",
                    "description": (
                        "With run_in_background: a regular expression for the line you are "
                        "waiting for (e.g. 'ready|listening on|compiled'). The first time the "
                        "task prints a matching line you are told, with that line, while it "
                        "keeps running — instead of sleeping and checking. If it ends first, "
                        "its end says the line never came."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

TASK_OUTPUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_task_output",
        "description": (
            "Read NEW output (since the last read) from a background task started with "
            "run_shell run_in_background=true, plus its status and exit code. You do NOT "
            "need this to learn when the task finishes — that arrives as a follow-up "
            "message. Use it only when you want the output before then, or to read more "
            "than the notification's tail. To wait for the task instead of calling again "
            "and again, pass `wait`: the call returns when the task finishes or after that "
            "many seconds, whichever is first. To find something in a long log, pass "
            "`pattern`: the matching lines of EVERYTHING the task printed, numbered; then "
            "`from_line` reads the whole output from one of those numbers. Neither moves "
            "the point the next plain read starts from."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by run_shell.",
                },
                "wait": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 120,
                    "description": "Seconds to wait for the task to finish before answering (0 = answer now).",
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "A regular expression: answer with the lines of the whole output "
                        "that match it, each with its line number (e.g. 'error|failed|Traceback')."
                    ),
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "With `pattern`: match regardless of letter case.",
                },
                "from_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Answer with the whole output from this line (1-based), as much as fits; "
                        "the result names the line to continue from."
                    ),
                },
            },
            "required": ["task_id"],
        },
    },
}

TASK_KILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_task_kill",
        "description": "Stop a background task started with run_shell run_in_background=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by run_shell.",
                }
            },
            "required": ["task_id"],
        },
    },
}


def run_shell_schema(shells: list[Shell], default: str) -> dict[str, Any]:
    """`run_shell`'s schema for the shells this world offers.

    One shell, or an executor that does not say, gets the schema as it always was. Two or more add a
    `shell` argument whose enum is exactly the kinds that exist, so the model cannot ask for a shell
    the machine lacks, and a machine with one shell pays no tokens for a choice."""
    schema = copy.deepcopy(RUN_SHELL_SCHEMA)
    if not shells:
        return schema
    function = schema["function"]
    function["description"] = function["description"].replace(WINDOWS_NOTE, shell_note(shells, default))
    if len(shells) > 1:
        function["parameters"]["properties"]["shell"] = {
            "type": "string",
            "enum": [shell.kind for shell in shells],
            "description": f"Which shell runs the command (default {default}).",
        }
    return schema
