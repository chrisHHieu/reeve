"""A Messages API reply (whole or streamed) as the loop's `AssistantTurn`."""

from __future__ import annotations

from typing import Any, Optional

from ..base import AssistantTurn, TokenUsage, ToolCall
from . import diagnostics
from .binding import dropped_by_api
from .refusal import finish_reason, raise_if_refused


def usage_from(usage: Any) -> Optional[TokenUsage]:
    """Token counts from a usage object. `input_tokens` already excludes the cached parts,
    which arrive as their own counters."""
    if usage is None:
        return None
    return TokenUsage(
        input=int(getattr(usage, "input_tokens", 0) or 0),
        output=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def reasoning_text(thinking: list[dict[str, Any]]) -> Optional[str]:
    """The thinking text shown to the person. Redacted blocks stay opaque."""
    text = "".join(block.get("thinking", "") for block in thinking if block.get("type") == "thinking")
    return text or None


def replay_extras(thinking: list[dict[str, Any]]) -> dict[str, Any]:
    """The sidecar that carries thinking blocks to the next request, where `convert` sends
    them back unchanged."""
    return {"_anthropic": {"blocks": thinking}} if thinking else {}


def thinking_block(block: Any) -> Optional[dict[str, Any]]:
    """A `thinking` or `redacted_thinking` block in the dict form that is stored and replayed
    (None for any other block)."""
    kind = getattr(block, "type", None)
    if kind == "thinking":
        return {
            "type": "thinking",
            "thinking": getattr(block, "thinking", "") or "",
            "signature": getattr(block, "signature", "") or "",
        }
    if kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": getattr(block, "data", "") or ""}
    return None


def turn_from_response(response: Any) -> AssistantTurn:
    text: list[str] = []
    calls: list[ToolCall] = []
    thinking: list[dict[str, Any]] = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            text.append(getattr(block, "text", "") or "")
        elif kind == "tool_use":
            calls.append(ToolCall(
                id=getattr(block, "id", "") or "",
                name=getattr(block, "name", "") or "",
                arguments=dict(getattr(block, "input", None) or {}),
            ))
        else:
            kept = thinking_block(block)
            if kept:
                thinking.append(kept)

    stop_reason = getattr(response, "stop_reason", None)
    raise_if_refused(stop_reason, response)
    return AssistantTurn(
        text="".join(text) or None,
        tool_calls=calls,
        finish_reason=finish_reason(stop_reason),
        raw=response,
        reasoning=reasoning_text(thinking),
        extras=replay_extras(thinking),
        usage=usage_from(getattr(response, "usage", None)),
        thinking_dropped=dropped_by_api(response) or None,
        response_id=getattr(response, "id", None),
        cache_miss=diagnostics.read(response),
    )
