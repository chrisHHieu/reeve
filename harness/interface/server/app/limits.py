"""Bounds on what one WebSocket client may send.

The loopback socket answers any local process, so frames, message text, attachments and the
request rate are all capped before anything is built into model content or a turn is started.
"""

from __future__ import annotations

from typing import Any

from harness.llm.attachments import MAX_ATTACHMENTS

MAX_FRAME_BYTES = 16 * 1024 * 1024
RATE_LIMIT_COUNT = 30
RATE_LIMIT_WINDOW_SECONDS = 10.0
MAX_MESSAGE_CHARS = 200_000
# Leaves room for the JSON around the attachments inside the frame cap.
MAX_ATTACHMENT_BYTES = 15_000_000
# One message names a handful of agents at most. The composer adds them one at a time, so the
# cap only matters for a crafted frame that would make the server resolve an endless list.
MAX_MENTIONS = 8

__all__ = [
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_FRAME_BYTES",
    "MAX_MENTIONS",
    "MAX_MESSAGE_CHARS",
    "RATE_LIMIT_COUNT",
    "RATE_LIMIT_WINDOW_SECONDS",
    "json_size",
]


def json_size(value: Any) -> int:
    """An upper estimate of the UTF-8 size of parsed JSON, without building the string."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(json_size(key) + json_size(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(json_size(item) for item in value)
    return 8  # numbers, booleans, null and the separators between them
