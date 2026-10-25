"""A streamed Messages API reply, read event by event into a turn."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Optional

from ..arguments import parse_arguments
from ..base import AssistantTurn, StreamChunk, TokenUsage, ToolCall
from . import context_editing
from .binding import dropped_by_api
from .refusal import finish_reason, raise_if_refused
from .turns import reasoning_text, replay_extras, thinking_block, usage_from


class _Reply:
    """What the events add up to so far."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.tool_input: dict[int, dict[str, str]] = {}
        # Thinking is kept per block index: it has to be sent back unchanged later, so both the
        # text and the signature (which arrives as a delta at the end) are collected.
        self.thinking: dict[int, dict[str, Any]] = {}
        self.stop_reason: Any = None
        self.usage: Optional[TokenUsage] = None
        self.last_delta: Any = None
        # What the API dropped from the request (`input_transformations`): on `message_start`, and
        # again on the closing `message_delta` after a mid-stream fallback, which then wins.
        self.dropped: dict[str, int] = {}

    def turn(self) -> AssistantTurn:
        raise_if_refused(self.stop_reason, self.last_delta)
        calls = [
            ToolCall(id=part["id"], name=part["name"], arguments=parse_arguments(part["json"]))
            for _, part in sorted(self.tool_input.items())
        ]
        blocks = [block for _, block in sorted(self.thinking.items())]
        return AssistantTurn(
            text="".join(self.text) or None,
            tool_calls=calls,
            finish_reason=finish_reason(self.stop_reason),
            reasoning=reasoning_text(blocks),
            extras=replay_extras(blocks),
            usage=self.usage,
            thinking_dropped=self.dropped or None,
        )


def _message_start(reply: _Reply, event: Any) -> None:
    # The prompt side of the counts (input and the cache split) opens the stream.
    message = getattr(event, "message", None)
    reply.usage = usage_from(getattr(message, "usage", None)) or reply.usage
    reply.dropped = dropped_by_api(message) or reply.dropped
    context_editing.log_applied(event)
    context_editing.log_applied(message)


def _block_start(reply: _Reply, event: Any) -> None:
    index = getattr(event, "index", 0)
    block = getattr(event, "content_block", None)
    if getattr(block, "type", None) == "tool_use":
        reply.tool_input[index] = {
            "id": getattr(block, "id", "") or "",
            "name": getattr(block, "name", "") or "",
            "json": "",
        }
        return
    kept = thinking_block(block)
    if kept:
        reply.thinking[index] = kept


def _block_delta(reply: _Reply, event: Any) -> Optional[StreamChunk]:
    index = getattr(event, "index", 0)
    delta = getattr(event, "delta", None)
    kind = getattr(delta, "type", None)
    if kind == "text_delta":
        text = getattr(delta, "text", "") or ""
        if text:
            reply.text.append(text)
            return StreamChunk(text_delta=text)
    elif kind == "input_json_delta":
        part = reply.tool_input.get(index)
        if part is not None:
            part["json"] += getattr(delta, "partial_json", "") or ""
    elif kind == "thinking_delta":
        block = reply.thinking.get(index)
        thought = getattr(delta, "thinking", "") or ""
        if block is not None and thought:
            block["thinking"] += thought
            return StreamChunk(reasoning_delta=thought)
    elif kind == "signature_delta":
        block = reply.thinking.get(index)
        if block is not None:
            block["signature"] = (block.get("signature") or "") + (getattr(delta, "signature", "") or "")
    return None


def _message_delta(reply: _Reply, event: Any) -> None:
    reply.last_delta = getattr(event, "delta", None)
    reply.dropped = dropped_by_api(event) or reply.dropped
    context_editing.log_applied(event)
    reason = getattr(reply.last_delta, "stop_reason", None)
    if reason:
        reply.stop_reason = reason

    # The closing counts are cumulative: output tokens always, and the prompt side when the
    # server puts it here. Some gateways open with input_tokens=0 and report the real prompt
    # only on this event. Reading the opening event alone made such a session look empty, and
    # compaction never fired.
    usage = getattr(event, "usage", None)
    output = int(getattr(usage, "output_tokens", 0) or 0)
    if output:
        reply.usage = reply.usage or TokenUsage()
        reply.usage.output = output
    prompt = {
        "input": getattr(usage, "input_tokens", None),
        "cache_read": getattr(usage, "cache_read_input_tokens", None),
        "cache_write": getattr(usage, "cache_creation_input_tokens", None),
    }
    if any(prompt.values()):
        reply.usage = reply.usage or TokenUsage()
        for name, value in prompt.items():
            if value is not None:
                setattr(reply.usage, name, int(value))


def read_stream(events: Iterable[Any]) -> Iterator[StreamChunk]:
    """Yield text and reasoning as they arrive, then one chunk carrying the finished turn."""
    reply = _Reply()
    for event in events:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            _message_start(reply, event)
        elif kind == "content_block_start":
            _block_start(reply, event)
        elif kind == "content_block_delta":
            chunk = _block_delta(reply, event)
            if chunk is not None:
                yield chunk
        elif kind == "message_delta":
            _message_delta(reply, event)
    yield StreamChunk(turn=reply.turn())
