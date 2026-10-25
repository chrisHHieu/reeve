"""`capture_result` — the tool that COMMITS a structured child result.

With an output schema, the discipline is that the child's answer is not "the text it
happened to end with" but an object it hands over through a tool call. The tool
validates at the moment of the call, so a wrong shape is a RETRYABLE TOOL ERROR inside
the child's own turn — it can fix the object and call again without the host spending a
corrective prompt round-trip — and a committed capture is authoritative once the turn
ends. The host still falls back to parsing the child's final text (and the one
corrective retry) for a child that answered without calling it.

The tool is registered per-child, with the schema bound, ONLY when `output_schema` was
asked for; it is never part of a normal session's toolset. After a capture commits, the
child's remaining calls are denied (`capture_decider`) — the commit is final, so the
only thing left for it to do is say what it produced.
"""

from __future__ import annotations

import json
from typing import Any

from aisuite.agents import ToolMetadata, tool

from .json_schema import validate_value

#: The name the child is told to call; the host reads the same string back.
CAPTURE_TOOL = "capture_result"


def capture_instruction(schema: dict) -> str:
    """The first prompt's structured-output instruction, capture-first."""
    return (
        f"Structured output required: when your work is done, call the `{CAPTURE_TOOL}` "
        "tool ONCE with the final JSON object as its `value` argument. It validates the "
        "object against the JSON Schema below and rejects it with the errors if it does "
        "not match — fix the object and call it again. After it accepts, reply with one "
        "short sentence and stop; do not call other tools.\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def capture_correction(schema: dict, errors: list[str]) -> str:
    """The one fallback retry, for a child that finished without committing a capture."""
    shown = "; ".join(errors[:5])
    return (
        f"You did not commit a valid structured result ({shown}). Call the "
        f"`{CAPTURE_TOOL}` tool with a JSON object that fixes those errors — no prose. "
        "If you cannot use the tool, reply with ONLY the valid JSON object.\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def capture_tool(schema: dict, sink: dict[str, Any]) -> object:
    """The tool bound to one child's schema. `sink["value"]` holds the committed object."""

    def capture_result(value: dict) -> dict:
        """Commit the final structured result for this task.

        Call it once, with the complete JSON object as `value`, only when the work is
        done. A rejection lists what does not match the schema and is retryable; after
        acceptance the result is final and later tool calls are refused.
        """
        errors = validate_value(schema, value)
        if errors:
            return {
                "error": (
                    "the captured object does not match the required schema: "
                    + "; ".join(errors[:5])
                    + f" — fix it and call {CAPTURE_TOOL} again with the corrected object."
                ),
                "retryable": True,
            }
        if sink.get("value") is None:
            sink["value"] = value
        return {
            "captured": True,
            "note": "The result is committed. Reply with one short sentence and stop.",
        }

    wrapped = tool(
        capture_result,
        metadata=ToolMetadata(
            category="output",
            risk_level="low",
            capabilities=["capture"],
            description="Commit the final structured result for this task.",
        ),
    )
    # The model-facing schema carries the REQUIRED SHAPE, not a bare `value: dict`: the
    # function-calling wire is the first place the child can see what is expected.
    wrapped.__tool_schema__ = {  # type: ignore[attr-defined]
        "type": "function",
        "function": {
            "name": CAPTURE_TOOL,
            "description": (
                "Commit the final structured result for this task. Call it once, with "
                "the complete JSON object as `value`, only when the work is done; a "
                "rejection is retryable, acceptance is final."
            ),
            "parameters": {
                "type": "object",
                "properties": {"value": dict(schema)},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }
    return wrapped


def capture_decider(inner: Any, sink: dict[str, Any]) -> Any:
    """Wrap a child's rule decider so a committed capture closes the turn: every later
    call except the capture tool itself is denied. Best effort within one parallel batch
    — this gate is consulted per call, before the call runs."""

    def decide(tool_name: str, arguments: dict) -> Any:
        if tool_name != CAPTURE_TOOL and sink.get("value") is not None:
            return "deny"
        return inner(tool_name, arguments)

    return decide
