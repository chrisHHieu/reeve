"""The conversation in the loop's own (chat-shaped) form, as Responses API `input` items.

What the Responses API wants differently from chat completions:

- The system prompt is the `instructions` field, not a role.
- A tool call is a top-level `function_call` item and its result a `function_call_output`
  item, paired by `call_id`. The ids only have to pair up, so ids made by another provider
  (`toolu_…`, after a switch mid-conversation) are fine.
- Tool schemas are flat: `{"type": "function", "name": …}` with no nested `function` key.
- Reasoning continuity: the raw output items (the reasoning item with `encrypted_content`, the
  `function_call` items with their ids) travel on the stored assistant message as the
  `_openai` sidecar. When it is there it is replayed unchanged, which keeps the chain of
  thought exact; when it is not (history from another provider), the items are rebuilt from the
  message's own fields.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            parts.append({"type": "input_text", "text": part.get("text") or ""})
        elif kind == "image_url":
            parts.append({"type": "input_image", "image_url": (part.get("image_url") or {}).get("url") or ""})
        elif kind == "file":
            file = part.get("file") or {}
            entry: dict[str, Any] = {"type": "input_file"}
            if file.get("filename"):
                entry["filename"] = file["filename"]
            if file.get("file_data"):
                entry["file_data"] = file["file_data"]
            parts.append(entry)
    return parts


def _rebuilt_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Items for an assistant message that has no usable `_openai` sidecar."""
    items: list[dict[str, Any]] = []
    if isinstance(message.get("content"), str) and message["content"]:
        items.append({"role": "assistant", "content": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {})
        items.append({
            "type": "function_call",
            "call_id": call.get("id") or "",
            "name": function.get("name") or "",
            "arguments": arguments,
        })
    return items


def convert_messages(messages: list[dict[str, Any]]) -> tuple[Optional[str], list[dict[str, Any]]]:
    """(`instructions`, Responses `input` items). Leading system messages are joined into the
    instructions; one in the middle of the thread stays as a system item."""
    leading = 0
    system: list[str] = []
    while leading < len(messages) and messages[leading].get("role") == "system":
        content = messages[leading].get("content")
        if isinstance(content, str) and content:
            system.append(content)
        leading += 1

    items: list[dict[str, Any]] = []
    for message in messages[leading:]:
        role = message.get("role")
        if role == "system":
            if message.get("content"):
                items.append({"role": "system", "content": message["content"]})
        elif role == "user":
            content = _user_content(message.get("content"))
            if content:
                items.append({"role": "user", "content": content})
        elif role == "assistant":
            replay = (message.get("_openai") or {}).get("items") or []
            items.extend(replay or _rebuilt_items(message))
        elif role == "tool":
            content = message.get("content")
            items.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id") or "",
                "output": content if isinstance(content, str) else str(content or ""),
            })
    return ("\n\n".join(system) or None), items


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Function schemas as flat Responses tool entries. A tool with no name is skipped."""
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        function = (tool or {}).get("function") or {}
        if not function.get("name"):
            continue
        # Not strict: the Responses API enforces a schema strictly unless told otherwise, and a
        # strict schema forbids every property it does not list. This harness's schemas are
        # written for the chat wire's default, where an object may carry more than it names
        # (`show_visual`'s blocks list only `type`): gpt-6-luna sent `{"blocks":[{"type":"chart"}]}`
        # ten times in a row in a real session (2026-10-09), the only shape strict mode let it send.
        entry: dict[str, Any] = {"type": "function", "name": function["name"], "strict": False}
        if function.get("description"):
            entry["description"] = function["description"]
        if function.get("parameters") is not None:
            entry["parameters"] = function["parameters"]
        converted.append(entry)
    return converted
