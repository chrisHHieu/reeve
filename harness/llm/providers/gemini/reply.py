"""A Gemini reply, whole or streamed, as the loop's `AssistantTurn`."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

from ..base import AssistantTurn, StreamChunk, TokenUsage, ToolCall

#: Gemini's `finishReason` in the loop's words. A turn that holds function calls is
#: `tool_calls` whatever Gemini says, since it has no separate reason for that.
_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "stop",
    "RECITATION": "stop",
    "MALFORMED_FUNCTION_CALL": "stop",
}


def _call_id() -> str:
    """Gemini's function calls have no ids, so one is made here. It must be unique across the
    whole session: numbering by position in the response restarted at `call_0` every step (65
    times in one real session), and everything that reads a transcript pairs a result with its
    call by id."""
    return f"call_{uuid.uuid4().hex[:24]}"


def usage_from(meta: Any) -> Optional[TokenUsage]:
    """Counts from `usage_metadata`. `prompt_token_count` includes the cached share, and
    thinking tokens are billed as output, so they are added to it."""
    if meta is None:
        return None
    prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
    cached = int(getattr(meta, "cached_content_token_count", 0) or 0)
    return TokenUsage(
        input=max(prompt - cached, 0),
        output=int(getattr(meta, "candidates_token_count", 0) or 0)
        + int(getattr(meta, "thoughts_token_count", 0) or 0),
        cache_read=cached,
    )


def _signature(part: Any) -> Optional[str]:
    """A part's thought signature as base64 text, safe to store as JSON. The SDK turns it back
    into bytes when it is sent."""
    signature = getattr(part, "thought_signature", None)
    if not signature:
        return None
    if isinstance(signature, (bytes, bytearray)):
        return base64.b64encode(bytes(signature)).decode("ascii")
    return str(signature)


def _signature_extras(text_signature: Optional[str], call_signatures: list[Optional[str]]) -> dict[str, Any]:
    """The `_gemini` sidecar for the stored assistant message (empty when nothing was signed)."""
    if not text_signature and not any(call_signatures):
        return {}
    return {"_gemini": {"text_sig": text_signature, "call_sigs": call_signatures}}


def _finish_reason(finish: Optional[str], has_calls: bool) -> Optional[str]:
    if has_calls:
        return "tool_calls"
    if finish is None:
        return None
    return _FINISH_REASONS.get(finish, finish.lower())


@dataclass
class _Candidate:
    """One response, or one streamed chunk, split by what each part is."""

    texts: list[str] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    finish: Optional[str] = None
    text_signature: Optional[str] = None
    call_signatures: list[Optional[str]] = field(default_factory=list)


def _read_candidate(response: Any) -> _Candidate:
    """Answer text, thought summaries, function calls (ids are made later), the finish reason
    and the signatures. A part flagged `thought` is reasoning: its signature is kept and its
    text never joins the answer."""
    out = _Candidate()
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return out
    candidate = candidates[0]
    for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
        signature = _signature(part)
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            out.calls.append(ToolCall(
                id="",
                name=getattr(function_call, "name", "") or "",
                arguments=dict(getattr(function_call, "args", None) or {}),
            ))
            out.call_signatures.append(signature)
            continue
        if signature:
            out.text_signature = signature
        text = getattr(part, "text", None)
        if getattr(part, "thought", False):
            if text:
                out.thoughts.append(text)
        elif text:
            out.texts.append(text)
    finish = getattr(candidate, "finish_reason", None)
    if finish is not None:
        out.finish = getattr(finish, "name", None) or str(finish)
    return out


def _with_ids(calls: list[ToolCall]) -> list[ToolCall]:
    return [ToolCall(id=_call_id(), name=call.name, arguments=call.arguments) for call in calls]


def turn_from_response(response: Any) -> AssistantTurn:
    read = _read_candidate(response)
    calls = _with_ids(read.calls)
    return AssistantTurn(
        text="".join(read.texts) or None,
        tool_calls=calls,
        finish_reason=_finish_reason(read.finish, bool(calls)),
        raw=response,
        reasoning="".join(read.thoughts) or None,
        extras=_signature_extras(read.text_signature, read.call_signatures),
        usage=usage_from(getattr(response, "usage_metadata", None)),
    )


def read_stream(chunks: Iterable[Any]) -> Iterator[StreamChunk]:
    """Yield reasoning and text as they arrive, then one chunk carrying the finished turn.

    Unlike Anthropic, a function call arrives whole (its arguments are a complete dict), so
    nothing is assembled from fragments; the parts are only collected across chunks.
    """
    text: list[str] = []
    thoughts: list[str] = []
    calls: list[ToolCall] = []
    call_signatures: list[Optional[str]] = []
    text_signature: Optional[str] = None
    finish: Optional[str] = None
    usage: Optional[TokenUsage] = None

    for chunk in chunks:
        # The counts are cumulative, so the last chunk that has any holds the total.
        counted = usage_from(getattr(chunk, "usage_metadata", None))
        if counted is not None:
            usage = counted
        read = _read_candidate(chunk)
        for thought in read.thoughts:
            thoughts.append(thought)
            yield StreamChunk(reasoning_delta=thought)
        for piece in read.texts:
            text.append(piece)
            yield StreamChunk(text_delta=piece)
        calls.extend(read.calls)
        call_signatures.extend(read.call_signatures)
        if read.text_signature:
            text_signature = read.text_signature
        if read.finish:
            finish = read.finish

    ready = _with_ids(calls)
    yield StreamChunk(turn=AssistantTurn(
        text="".join(text) or None,
        tool_calls=ready,
        finish_reason=_finish_reason(finish, bool(ready)),
        reasoning="".join(thoughts) or None,
        extras=_signature_extras(text_signature, call_signatures),
        usage=usage,
    ))
