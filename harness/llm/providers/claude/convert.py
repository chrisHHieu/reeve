"""The conversation in the loop's own (OpenAI-shaped) form, as a Messages API request.

What the API wants differently from chat completions, and what this module does about it:

- `system` is a top-level field, not a role: the leading system messages become it.
- A tool call is a `tool_use` block whose input is a dict; the loop stores a JSON string.
- Tool results are `tool_result` blocks and all of one turn's results must sit in the single
  next user message, so a run of `tool` messages is folded into one.
- Thinking blocks must be sent back unchanged, signature included, ahead of the turn's
  tool calls. They travel on the stored assistant message as the `_anthropic` sidecar, with the
  prefix they were written after (`binding.py`).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..arguments import parse_arguments
from .binding import PREFIX_KEY

_IMAGE_DATA_URL = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_PDF_DATA_URL = re.compile(r"^data:application/pdf;base64,(.+)$", re.IGNORECASE | re.DOTALL)


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _image(url: str) -> Optional[dict[str, Any]]:
    """An `image_url` part as an image block. Attachments are data URLs; a plain http(s) URL
    is passed as a URL source; anything else cannot be sent."""
    found = _IMAGE_DATA_URL.match(url)
    if found:
        source = {"type": "base64", "media_type": found.group(1).lower(), "data": found.group(2)}
        return {"type": "image", "source": source}
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _document(part: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A `file` part carrying a PDF data URL as a document block."""
    file = part.get("file") or {}
    found = _PDF_DATA_URL.match(file.get("file_data") or "")
    if not found:
        return None
    block: dict[str, Any] = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": found.group(1)},
    }
    if file.get("filename"):
        block["title"] = str(file["filename"])
    return block


def _user_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [_text(content)] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            if part.get("text"):
                blocks.append(_text(part["text"]))
        elif kind == "image_url":
            blocks.append(_image((part.get("image_url") or {}).get("url") or "")
                          or _text("[unsupported image attachment]"))
        elif kind == "file":
            blocks.append(_document(part) or _text("[unsupported file attachment]"))
    return blocks


def _assistant_content(message: dict[str, Any]) -> list[dict[str, Any]]:
    sidecar = message.get("_anthropic") or {}
    # Each block takes the prefix it was written after along, for `binding.keep_bound`, which reads
    # the key and takes it off before the request is sent.
    prefix = sidecar.get("prefix")
    blocks = [{**block, PREFIX_KEY: prefix} if prefix else dict(block) for block in sidecar.get("blocks") or []]
    if isinstance(message.get("content"), str) and message["content"]:
        blocks.append(_text(message["content"]))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        blocks.append({
            "type": "tool_use",
            "id": call.get("id") or "",
            "name": function.get("name") or "",
            "input": parse_arguments(function.get("arguments")),
        })
    return blocks


def convert_messages(messages: list[dict[str, Any]]) -> tuple[Optional[str], list[dict[str, Any]]]:
    """(`system`, `messages`) for the Messages API.

    Neighbours with the same role are merged. That is what turns the run of `tool` messages
    (one per parallel call) into the one user message the API requires, with any user text
    that follows it in the same message.
    """
    leading = 0
    system: list[str] = []
    while leading < len(messages) and messages[leading].get("role") == "system":
        content = messages[leading].get("content")
        if isinstance(content, str) and content:
            system.append(content)
        leading += 1

    converted: list[dict[str, Any]] = []
    for message in messages[leading:]:
        role = message.get("role")
        if role == "system":
            # A system message in the middle of the thread is kept, as marked user text.
            if message.get("content"):
                converted.append({"role": "user",
                                  "content": [_text(f"<system>\n{message['content']}\n</system>")]})
        elif role == "user":
            blocks = _user_content(message.get("content"))
            if blocks:
                converted.append({"role": "user", "content": blocks})
        elif role == "assistant":
            blocks = _assistant_content(message)
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            converted.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id") or "",
                "content": str(message.get("content") or ""),
            }]})

    merged: list[dict[str, Any]] = []
    for message in converted:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"].extend(message["content"])
        else:
            merged.append(message)

    if not merged:
        raise ValueError("no convertible messages for the Anthropic Messages API")
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [_text("(continued)")]})
    return ("\n\n".join(system) or None), merged


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Function schemas as Anthropic tool definitions. A missing description is left out; a
    missing or typeless parameter schema becomes an empty object schema, which the API
    insists on."""
    converted = []
    for tool in tools or []:
        function = tool.get("function") or {}
        entry: dict[str, Any] = {"name": function.get("name") or ""}
        if function.get("description"):
            entry["description"] = function["description"]
        schema = function.get("parameters")
        if not isinstance(schema, dict) or not schema.get("type"):
            schema = {"type": "object", "properties": {}}
        entry["input_schema"] = schema
        converted.append(entry)
    return converted
