"""Server-side context editing (the `context-management-2025-06-27` beta).

The API clears old tool results and thinking blocks from the prompt it reads, without our
stored history changing. It is the cheap rung of the context-cost ladder: it works on every
request past the trigger, so a long session stops growing between full compactions.

It is configured by the `[context_editing]` table, off with `enabled = false`, and it turns
itself off for the process when a model or gateway answers 400 to it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("harness.llm.providers.anthropic")

BETA = "context-management-2025-06-27"

THINKING_EDIT = "clear_thinking_20251015"
TOOL_EDIT = "clear_tool_uses_20250919"

#: `trigger_input_tokens` is the API's own default. `keep_tool_uses` leaves the newest three
#: results readable, the ones a working turn is still using. `clear_at_least_tokens` guards
#: the prompt cache: clearing breaks the cached prefix, so a pass that frees less than this
#: is not worth it (0 hands the decision to the API).
CONTEXT_EDITING_DEFAULTS: dict[str, Any] = {
    "trigger_input_tokens": 100_000,
    "keep_tool_uses": 3,
    "clear_at_least_tokens": 15_000,
    "exclude_tools": [],
    "clear_tool_inputs": False,
    "clear_thinking": True,
}


def normalize_context_editing(raw: Any) -> Optional[dict[str, Any]]:
    """The `[context_editing]` table as an effective spec, or None when editing is off.

    A bad value in one knob falls back to that knob's default with a warning: a malformed
    optimisation must not fail a model call.
    """
    if raw is None or raw is False:
        return None
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        logger.warning("config: [context_editing] must be a table; ignoring %r", raw)
        return None
    if raw.get("enabled") is False:
        return None

    spec = dict(CONTEXT_EDITING_DEFAULTS)

    def number(key: str, floor: int) -> int:
        value = raw.get(key, spec[key])
        try:
            return max(floor, int(value))
        except (TypeError, ValueError):
            logger.warning(
                "config: [context_editing] %s=%r is not a number; using %d", key, value, spec[key]
            )
            return spec[key]

    spec["trigger_input_tokens"] = number("trigger_input_tokens", 1_000)
    spec["keep_tool_uses"] = number("keep_tool_uses", 0)
    spec["clear_at_least_tokens"] = number("clear_at_least_tokens", 0)
    spec["clear_tool_inputs"] = bool(raw.get("clear_tool_inputs", spec["clear_tool_inputs"]))
    spec["clear_thinking"] = bool(raw.get("clear_thinking", spec["clear_thinking"]))

    excluded = raw.get("exclude_tools", spec["exclude_tools"])
    if isinstance(excluded, (list, tuple)):
        spec["exclude_tools"] = [str(name).strip() for name in excluded if str(name).strip()]
    else:
        logger.warning("config: [context_editing] exclude_tools must be a list; ignoring %r", excluded)
        spec["exclude_tools"] = []
    return spec


def context_editing_edits(spec: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """The `context_management.edits` list for one request: thinking first, as the API
    documents, then tool results."""
    if not spec:
        return []
    edits: list[dict[str, Any]] = []
    if spec.get("clear_thinking"):
        edits.append({"type": THINKING_EDIT, "keep": "all"})
    tools: dict[str, Any] = {
        "type": TOOL_EDIT,
        "trigger": {"type": "input_tokens", "value": int(spec["trigger_input_tokens"])},
        "keep": {"type": "tool_uses", "value": int(spec["keep_tool_uses"])},
        "clear_tool_inputs": bool(spec.get("clear_tool_inputs")),
    }
    if int(spec.get("clear_at_least_tokens") or 0) > 0:
        tools["clear_at_least"] = {"type": "input_tokens", "value": int(spec["clear_at_least_tokens"])}
    if spec.get("exclude_tools"):
        tools["exclude_tools"] = list(spec["exclude_tools"])
    edits.append(tools)
    return edits


def is_rejection(exc: Exception) -> bool:
    """Whether a 400 is about context editing itself (a model or gateway that does not serve
    it) and not about the request. Only a 400 that names one of the edit fields counts; any
    other failure keeps its own handling."""
    if type(exc).__name__ != "BadRequestError":
        return False
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("context_management", "context management", "clear_tool_uses", "clear_thinking")
    )


def log_applied(source: Any) -> None:
    """Say what the server cleared, when the reply reports it. This is the number the feature
    is judged by: prompt size and cache hits on either side of it."""
    payload = getattr(source, "context_management", None)
    if payload is None:
        return
    edits = getattr(payload, "applied_edits", None)
    if edits is None and isinstance(payload, dict):
        edits = payload.get("applied_edits")
    for edit in edits or []:
        if isinstance(edit, dict):
            cleared, tokens = edit.get("cleared_tool_uses"), edit.get("cleared_input_tokens")
        else:
            cleared = getattr(edit, "cleared_tool_uses", None)
            tokens = getattr(edit, "cleared_input_tokens", None)
        if cleared or tokens:
            logger.info(
                "context editing cleared %s tool uses / %s input tokens from the prompt",
                "?" if cleared is None else cleared,
                "?" if tokens is None else tokens,
            )
