"""A chat-completions reply, whole or streamed, as the loop's `AssistantTurn`."""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator, Optional

from ..base import AssistantTurn, StreamChunk, TokenUsage, ToolCall, ToolCallDelta
from .salvage import maybe_salvage_tool_calls
from .usage import usage_from


def reasoning_of(obj: Any) -> Optional[str]:
    """Thinking text on a message or delta: `reasoning_content` (DeepSeek, GLM, Kimi and most
    compatible vendors) or `reasoning` (xAI, OpenRouter). The SDK's models keep extra response
    fields, so a plain getattr finds them."""
    value = getattr(obj, "reasoning_content", None) or getattr(obj, "reasoning", None)
    return value if isinstance(value, str) and value else None


def _arguments(raw: Any) -> Any:
    """Arguments from the JSON text the model wrote. Text that does not parse is kept under
    `_raw` rather than dropping the call, so the loop can return a tool error and let the
    model correct itself."""
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        return {"_raw": raw}


def parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    return [
        ToolCall(
            id=getattr(call, "id", ""),
            name=call.function.name,
            arguments=_arguments(getattr(call.function, "arguments", None)),
        )
        for call in raw_calls or []
    ]


def turn_from_response(response: Any, *, tools: Optional[list[dict[str, Any]]]) -> AssistantTurn:
    choice = response.choices[0]
    message = choice.message
    text, calls = maybe_salvage_tool_calls(
        getattr(message, "content", None),
        parse_tool_calls(getattr(message, "tool_calls", None)),
        tools=tools,
    )
    return AssistantTurn(
        text=text,
        tool_calls=calls,
        finish_reason=getattr(choice, "finish_reason", None),
        raw=response,
        reasoning=reasoning_of(message),
        usage=usage_from(getattr(response, "usage", None)),
    )


def read_stream(chunks: Iterable[Any], *, tools: Optional[list[dict[str, Any]]]) -> Iterator[StreamChunk]:
    """Yield text, reasoning and tool-call fragments as they arrive, then one chunk carrying
    the finished turn."""
    text: list[str] = []
    reasoning: list[str] = []
    pending: dict[int, dict[str, str]] = {}
    finish_reason = None
    usage: Optional[TokenUsage] = None
    # Aggregators such as OpenRouter name the upstream that served the call on each chunk;
    # other servers send nothing and this stays None.
    upstream: Optional[str] = None

    for chunk in chunks:
        if upstream is None:
            served_by = getattr(chunk, "provider", None)
            if isinstance(served_by, str) and served_by:
                upstream = served_by
        counted = usage_from(getattr(chunk, "usage", None))
        if counted is not None:
            usage = counted
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            thought = reasoning_of(delta)
            if thought:
                reasoning.append(thought)
                yield StreamChunk(reasoning_delta=thought)
            content = getattr(delta, "content", None)
            if content:
                text.append(content)
                yield StreamChunk(text_delta=content)
            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", 0)
                part = pending.setdefault(index, {"id": "", "name": "", "args": ""})
                if getattr(call, "id", None):
                    part["id"] = call.id
                function = getattr(call, "function", None)
                fragment = ""
                if function is not None:
                    if getattr(function, "name", None):
                        part["name"] = function.name
                    if getattr(function, "arguments", None):
                        part["args"] += function.arguments
                        fragment = function.arguments
                # The fragment is forwarded as well as accumulated, so a surface can show the
                # call being written instead of waiting for the step to end. It is sent even
                # when empty: the frame that carries the name carries no arguments, and the
                # name is the half a person can read.
                yield StreamChunk(tool_call_delta=ToolCallDelta(
                    index=index, id=part["id"], name=part["name"], arguments_delta=fragment
                ))
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason

    calls = [
        ToolCall(id=part["id"], name=part["name"], arguments=_arguments(part["args"]))
        for _, part in sorted(pending.items())
    ]
    answer, calls = maybe_salvage_tool_calls("".join(text) or None, calls, tools=tools)
    yield StreamChunk(turn=AssistantTurn(
        text=answer,
        tool_calls=calls,
        finish_reason=finish_reason,
        reasoning="".join(reasoning) or None,
        usage=usage,
        upstream=upstream,
    ))
