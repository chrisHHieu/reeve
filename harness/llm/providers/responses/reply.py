"""A Responses API reply, whole or streamed, as the loop's `AssistantTurn`."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Optional

from ..arguments import parse_arguments
from ..base import AssistantTurn, StreamChunk, TokenUsage, ToolCall


def plain(value: Any) -> Any:
    """An output item (SDK model, dict, or a test's namespace object) as plain data that can be
    written to the transcript."""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    if hasattr(value, "__dict__"):
        return {key: plain(item) for key, item in vars(value).items() if item is not None}
    return value


def usage_from(usage: Any) -> Optional[TokenUsage]:
    """`input_tokens` includes the cached share, so fresh input is the difference (the same
    convention as the other adapters); `output_tokens` already counts reasoning. Reads are
    defensive because compatible or older servers may leave out `input_tokens_details`."""
    if usage is None:
        return None
    cached = int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0)
    return TokenUsage(
        input=max(int(getattr(usage, "input_tokens", 0) or 0) - cached, 0),
        output=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read=cached,
    )


def _replay_extras(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The `_openai` sidecar for the stored message, or {} when replaying would add nothing.

    A reasoning item without `encrypted_content` is left out: with `store: false` the server
    cannot find it by id and would reject the replay. The sidecar is kept only when something
    beyond plain answer text needs continuity.
    """
    kept = [item for item in items if item.get("type") != "reasoning" or item.get("encrypted_content")]
    if any(item.get("type") in ("reasoning", "function_call") for item in kept):
        return {"_openai": {"items": kept}}
    return {}


def turn_from_response(response: Any) -> AssistantTurn:
    items = [plain(item) for item in getattr(response, "output", None) or []]
    text: list[str] = []
    summaries: list[str] = []
    calls: list[ToolCall] = []
    for item in items:
        kind = item.get("type")
        if kind == "message" or (kind is None and "content" in item):
            content = item.get("content")
            if isinstance(content, str):
                text.append(content)
            else:
                text.extend(part["text"] for part in content or []
                            if part.get("type") == "output_text" and part.get("text"))
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                summary = part.get("text") if isinstance(part, dict) else part
                if summary:
                    summaries.append(summary)
        elif kind == "function_call":
            calls.append(ToolCall(
                id=item.get("call_id") or item.get("id") or "",
                name=item.get("name") or "",
                arguments=parse_arguments(item.get("arguments")),
            ))

    incomplete = plain(getattr(response, "incomplete_details", None)) or {}
    if calls:
        finish = "tool_calls"
    elif incomplete.get("reason") == "max_output_tokens":
        finish = "length"
    else:
        finish = "stop"
    return AssistantTurn(
        text="".join(text) or None,
        tool_calls=calls,
        finish_reason=finish,
        raw=response,
        reasoning="".join(summaries) or None,
        extras=_replay_extras(items),
        usage=usage_from(getattr(response, "usage", None)),
    )


def read_stream(events: Iterable[Any]) -> Iterator[StreamChunk]:
    """Yield text and reasoning summaries as they arrive, then one chunk with the finished turn."""
    text: list[str] = []
    reasoning: list[str] = []
    finished_items: list[Any] = []
    final: Any = None
    for event in events:
        kind = getattr(event, "type", None)
        if kind == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                text.append(delta)
                yield StreamChunk(text_delta=delta)
        elif kind == "response.reasoning_summary_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                reasoning.append(delta)
                yield StreamChunk(reasoning_delta=delta)
        elif kind == "response.output_item.done":
            item = getattr(event, "item", None)
            if item is not None:
                finished_items.append(item)
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            final = getattr(event, "response", None)

    if final is None:
        yield StreamChunk(turn=AssistantTurn(text="".join(text) or None, reasoning="".join(reasoning) or None))
        return
    # The closing event carries the whole response, so the turn is read from it in one place.
    # Some Responses-compatible backends (the subscription one) leave its `output` empty and
    # only stream the items, so the streamed items are put back first. Without that, the
    # turn's text and tool calls would vanish.
    if not (getattr(final, "output", None) or []) and finished_items:
        try:
            final.output = finished_items
        except Exception:
            pass
    turn = turn_from_response(final)
    if turn.text is None and not turn.tool_calls and text:
        turn.text = "".join(text)
    yield StreamChunk(turn=turn)
