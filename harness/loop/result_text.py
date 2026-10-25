"""How a tool result that reports its own failure is read: as an error on its log row
(`is_error`) and as text in the conversation."""

from __future__ import annotations

import json
from typing import Any, Optional


def _reports_error(value: Any) -> bool:
    """Whether a tool's own result says the call failed: a dict whose `error` is text.

    A dispatch that returned is `status: ok` even when what it returned is a refusal — an
    MCP server's `{"error": …}`, a goal that would not close, an image that could not be
    read. Counted 2026-10-06: 44 such rows since 2026-09-25 beside the 81 the log called
    errors, so a third of the failures were invisible to anything that counts them.
    """
    return isinstance(value, dict) and isinstance(value.get("error"), str) and bool(value["error"].strip())


def _refusal_text(value: Any) -> Optional[str]:
    """A result that reports its own failure, as the text every other failure reads as:
    `Error: <what>`, then any other field the tool gave as one line each.

    A tool with no renderer of its own was JSON-encoded, so its refusal reached the model
    as `{"error": …}` while every rendered tool said `Error: …`. Counted 2026-10-06: 82
    such results since 2026-09-25 (an MCP server's error, a goal that would not close, a
    blocked write). A field that only repeats the error sentence (a block's `reason`
    and `rule`) is not said twice."""
    if not _reports_error(value):
        return None
    head = value["error"].strip()
    lines = [head if head.lower().startswith("error") else f"Error: {head}"]
    for key, item in value.items():
        if key == "error" or str(key).startswith("_") or item in (None, "", [], {}):
            continue
        text = item if isinstance(item, str) else json.dumps(item, default=str, ensure_ascii=False)
        if isinstance(item, str) and item.strip() and item.strip() in head:
            continue
        lines.append(f"{key}: {text}")
    return "\n".join(lines)


__all__ = ["_refusal_text", "_reports_error"]
