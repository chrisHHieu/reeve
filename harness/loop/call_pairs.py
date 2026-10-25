"""Which tool call a tool result answers, read by position.

A tool-call id is unique only within the response that made it. The Gemini client names its calls
`call_<n>` by position in a response, so every step of a session starts again at `call_0`: a real session had
65 of them. A table built over the whole transcript (`id -> call`) keeps the LAST call with an id, so every
earlier result resolved to whatever came last. The goal gate went blind this way: the `done` call itself was the
newest `call_0`, every shell result before it read as "goal", and `done` was refused as "has not been run" for
checks that had run (session b3a8491e-9db, 2026-10-01).

A result always follows the call it answers, so the call a result belongs to is the NEWEST one before it with
its id. That holds for any transcript, new ids or old, and is the one rule every reader of the transcript uses.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Sequence


def answered_calls(
    messages: Sequence[Mapping[str, Any]],
) -> Iterator[tuple[int, Mapping[str, Any], Mapping[str, Any] | None]]:
    """`(index, message, call)` for every message, in order.

    For a tool result, `call` is the assistant tool call it answers (the newest one before it with its
    `tool_call_id`), or None when nothing before it carries that id. For any other message it is None.
    """
    seen: dict[str, Mapping[str, Any]] = {}
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                seen[str(call.get("id") or "")] = call
            yield index, message, None
        elif role == "tool":
            yield index, message, seen.get(str(message.get("tool_call_id") or ""))
        else:
            yield index, message, None


def call_name(call: Mapping[str, Any] | None) -> str:
    """The tool a call names, or "" for no call."""
    return str(((call or {}).get("function") or {}).get("name") or "")
