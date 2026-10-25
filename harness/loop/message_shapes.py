"""The messages the engine writes into history, and the projections that keep them well formed."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from harness.config.known_secrets import redact
from harness.llm.providers import AssistantTurn, ToolCall
from harness.session.turns import ATTACHED_BY

MANGLED_PREVIEW_CHARS = 200

# What the wire says for a call whose result was never recorded. It is worded as a fact and not an
# error: the call may or may not have run, and the model should check and not assume either.
ORPHANED_CALL_RESULT = json.dumps(
    {
        "error": "no result was recorded for this call",
        "reason": "it was interrupted before it finished; check its effect before relying on it",
    }
)


# -- projections over history --------------------------------------------------------------


def order_tool_responses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make every tool message answering an assistant turn follow that turn directly.

    A tool result's attachment (the picture `read_image` fetched, which rides a user message because
    no provider accepts an image inside a tool result) used to be appended right after its own
    result. With two parallel calls the order was `assistant(A,B), tool(A), user(image A), tool(B),
    user(image B)`. DeepSeek rejects that outright ("insufficient tool messages following tool_calls
    message") and the lenient providers quietly tolerate it. This moves the attachments after the
    last tool result of their turn, which gives the one order every provider states: assistant,
    tool(A), tool(B), attachment(A), attachment(B).

    It is a pure projection: the canonical history keeps whatever shape it has, so old sessions with
    the old order are repaired on the wire and nothing on disk is rewritten. A run genuinely missing
    a result is left alone (that is the orphan case, not this one), and a real user or assistant
    message ends the run."""
    ordered: list[dict[str, Any]] = []
    at = 0
    while at < len(messages):
        message = messages[at]
        ordered.append(message)
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            at += 1
            continue
        call_ids = {call.get("id") for call in calls if call.get("id")}
        awaiting = set(call_ids)
        results: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        at += 1
        while at < len(messages):
            following = messages[at]
            if following.get("role") == "tool" and following.get("tool_call_id") in awaiting:
                results.append(following)
                awaiting.discard(following.get("tool_call_id"))
            elif following.get("role") == "user" and following.get(ATTACHED_BY) in call_ids:
                attachments.append(following)
            else:
                break
            at += 1
        ordered.extend(results)
        ordered.extend(attachments)
    return ordered


def answer_orphaned_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every tool call shown to the provider a result right after its assistant message.

    A call can lose its result in history: the process was killed while the tool ran, or the model
    was switched under a pending call. Hosted chat templates reject that sequence outright
    (DeepSeek: "An assistant message with 'tool_calls' must be followed by tool messages responding
    to each 'tool_call_id'"), and since a request carries the whole history, every request fails,
    including a compaction's summarizer call: session b3a8491e-9db could not compact for it
    (2026-10-01). So each unanswered id gets a stub result at the end of its run, in the projection
    only.

    Like `order_tool_responses` it is pure: the canonical history keeps what happened, and a
    history with no orphan comes back unchanged, so the bytes a provider cached stay the same.
    Pairing is by position (`call_pairs`), because ids repeat across a session and only the run right
    after the assistant message can answer its calls. Run it on the final list, with notices dropped
    and attachments ordered, where adjacency is what the provider will see."""
    out: list[dict[str, Any]] = []
    at = 0
    while at < len(messages):
        message = messages[at]
        out.append(message)
        at += 1
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            continue
        answered: set[str] = set()
        while at < len(messages) and messages[at].get("role") == "tool":
            answered.add(str(messages[at].get("tool_call_id") or ""))
            out.append(messages[at])
            at += 1
        for call in calls:
            call_id = str(call.get("id") or "")
            if call_id and call_id not in answered:
                out.append({"role": "tool", "tool_call_id": call_id, "content": ORPHANED_CALL_RESULT})
                answered.add(call_id)
    return out


# -- messages ------------------------------------------------------------------------------


def assistant_message(
    turn: AssistantTurn,
    model: Optional[str] = None,
    timing: Optional[dict[str, float]] = None,
    context_breakdown: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or "", "ts": time.time()}
    if timing:
        # A display sidecar like `usage`: when the request went out, when the first token came back
        # and when the stream ended. Time to first token and decode speed cannot be derived from
        # `ts`, which is the moment the message was appended, the end of a step, and says nothing
        # about how the time inside it was spent.
        message["timing"] = dict(timing)
    if turn.usage is not None:
        # Persisted with the message and stripped before provider calls. It carries the producing
        # model, so per-model totals survive a model switch mid-session.
        message["usage"] = {"model": model, **turn.usage.as_dict()}
        # The per-category context estimate rides inside the usage sidecar (additive, stripped with
        # it). It is None on turns whose request was not measured.
        if context_breakdown is not None:
            message["usage"]["context_breakdown"] = dict(context_breakdown)
    if turn.reasoning:
        # Thinking text for display, which the GUI shows and every provider call strips
        # (`_outbound_messages`). Provider-private replay blocks travel in `extras`.
        message["reasoning"] = turn.reasoning
    if turn.extras:
        # Provider-private sidecars (Gemini's thought signatures, say) persist with the message.
        # The owning provider puts them back and the others strip them (base.py).
        message.update(turn.extras)
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in turn.tool_calls
        ]
    return message


def tool_result_message(
    tool_call: ToolCall,
    result: Any,
    rendered: Optional[str] = None,
    reminder: Optional[str] = None,
    timing: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """The tool result as the model sees it.

    `rendered` is the tool contract's own projection (tools/contract.py); without one the value is
    JSON-encoded as it always was. `reminder` is the repeat guard's note, placed after the result and
    set apart from it so the model does not read it as something the tool returned."""
    if rendered is not None:
        content = rendered
    else:
        content = result if isinstance(result, str) else json.dumps(result, default=str)
    if reminder:
        content = f"{content}\n\n{reminder}"
    # A secret this process holds (the server token, a stored key) is not shown to the model, so it
    # is never copied into a later command either (config/known_secrets.py).
    content = redact(content)
    message: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": content,
        "ts": time.time(),
    }
    if timing:
        # The wall time of the call itself and not of the gap around it. Differences in `ts` would
        # charge this tool for the approval it waited on and for every sibling running beside it,
        # which is backwards for the one number people look at.
        message["timing"] = dict(timing)
    return message


def tool_error_message(tool_call: ToolCall, reason: str) -> dict[str, Any]:
    # Text, like every other failure the model reads (`refusal_text`); it used to be the JSON
    # object `{"error": "tool call not executed", "reason": …}`.
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": f"Error: tool call not executed: {reason}",
        "ts": time.time(),
    }


# -- tool calls the provider could not parse ------------------------------------------------


def is_mangled(tool_call: ToolCall) -> bool:
    """A provider's argument parser falls back to `{"_raw": <unparsed text>}` when the arguments are
    not a JSON object, typically because the stream was cut in the middle of them."""
    return set(tool_call.arguments or {}) == {"_raw"}


def sanitize_mangled_calls(turn: AssistantTurn) -> None:
    """Shorten each mangled call's stored raw text to a preview before the turn enters history.

    The full text is junk, half a JSON document. Replaying it costs thousands of tokens per turn and
    teaches the model that `_raw` is a real parameter shape to imitate."""
    for call in turn.tool_calls:
        if not is_mangled(call):
            continue
        raw = str(call.arguments.get("_raw") or "")
        if len(raw) > MANGLED_PREVIEW_CHARS:
            call.arguments = {
                "_raw": raw[:MANGLED_PREVIEW_CHARS]
                + f"… [unparsed tool-call text, {len(raw)} chars, truncated in history]"
            }


# -- text for logs and events ---------------------------------------------------------------


def preview(value: Any, max_chars: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def log_text(value: Any) -> str:
    """The text of one event field. The log is read to find which step went wrong, so a structure
    is rendered compactly and not pretty-printed. `session_log` bounds the field, and this only
    decides its shape."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(part.get("text", "[image]")) if isinstance(part, dict) else str(part) for part in value
        )
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def context_digest(stable: str) -> str:
    """A comparison key for the non-clock context: a digest and not the text, because the sidecar
    rides every persisted message and the only question is whether it is the same or changed."""
    return hashlib.blake2b(stable.encode("utf-8", "replace"), digest_size=16).hexdigest()


# -- what a child run reports ---------------------------------------------------------------


def delegated_tokens(result: Any) -> int:
    """What a child run cost, when the tool result reports it. Only the delegating tools report a
    `tokens` count, so every other call gives 0, which is the right answer for a tool that spent
    nothing and is why there is no separate "absent" case."""
    if not isinstance(result, dict):
        return 0
    tokens = result.get("tokens")
    return tokens if isinstance(tokens, int) and tokens > 0 else 0


def delegation_ref(result: Any) -> Optional[dict]:
    """The child run a tool result came back from, when it names one.

    Both spellings are one fact: a foreground child reports `run_id`, and a detached child is
    addressed by the `task_id` it was handed at spawn, which is its run id, so the two ends of a
    detached run (the spawn and the later read) join on one value. Every other tool returns None,
    leaving the field absent and not empty on the forty-odd calls that never spawned anything."""
    if not isinstance(result, dict):
        return None
    run_id = result.get("run_id") or result.get("task_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    ref: dict[str, Any] = {"run_id": run_id}
    status = result.get("status")
    if isinstance(status, str) and status:
        ref["status"] = status
    return ref
