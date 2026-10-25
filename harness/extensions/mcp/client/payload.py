"""Turning a tool's reply into something the engine can serialize for the model."""

from __future__ import annotations

from typing import Any


def result_payload(result: Any) -> Any:
    """Flatten a `CallToolResult`.

    Text blocks become the answer. Image blocks (the `computer-use` sidecar's screenshot and zoom)
    are lifted into `_attach`, the channel `read_image` uses, so the engine hands them to the model
    as pictures and the transcript can draw them, in place of the old `[image]` placeholder.
    `_attach` rides a user-role message, and the string half stays the model-facing tool result."""
    texts: list[str] = []
    attachments: list[dict[str, Any]] = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "image":
            data = getattr(block, "data", None)
            mime = getattr(block, "mimeType", None) or "image/png"
            if isinstance(data, str) and data:
                attachments.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            else:
                texts.append("[image]")
            continue
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
        else:  # a block with no mapping, described by its type
            texts.append(f"[{getattr(block, 'type', 'content')}]")
    body = "\n".join(texts)
    if getattr(result, "isError", False):
        return {"error": body or "MCP tool error"}
    if attachments:
        return {"text": body, "_attach": attachments}
    structured = getattr(result, "structuredContent", None)
    if structured is not None and not body:
        return structured
    return body
