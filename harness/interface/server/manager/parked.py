"""Helpers shared by every place that parks a question for the person.

A prompt is an inbox item that the agent awaits. The live socket (`app/prompts.py`) and the
background runs (`inbox_handlers.py`) build the same items and read the same replies, so what
they share lives here once.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness.integrations.inbox import args_preview


def approval_body(request) -> str:
    """The body of an approval card: the tool's reason, if any, then a compact preview of its
    arguments, so a mirrored "Run `write_file`?" shows the path and content and not only the
    tool name."""
    reason = (getattr(request, "reason", "") or "").strip()
    preview = args_preview(getattr(request, "arguments", None))
    return "\n".join(part for part in (reason, preview) if part)


def parse_resolution(raw: str) -> dict[str, Any]:
    """The reply of a prompt that carries structure (folder, plan, ...) as a JSON string."""
    try:
        value = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
