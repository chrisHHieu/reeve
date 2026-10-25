"""What one field of a session-log row may hold: bounded in length, and free of the secrets the
process holds (`config/known_secrets.py`). Split from `session_log.py` (size ceiling)."""

from __future__ import annotations

from typing import Any

from harness.config.known_secrets import redact_all

#: Per-event payload ceiling. A tool result can be 20k characters and there is no reason to
#: store a second copy of it here at full size — the message list already has it, and this
#: log is read to find WHICH step was slow, not to re-read its output.
MAX_FIELD_CHARS = 4_000


def _clip(value: Any) -> Any:
    """Bound one field. Long text keeps its head and says how much went — the same shape
    every other bound in this codebase uses, for the same reason: a value silently cut is
    one the reader reasons about as though it were whole."""
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        dropped = len(value) - MAX_FIELD_CHARS
        return value[:MAX_FIELD_CHARS] + f"\n…[{dropped:,} more characters]"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


def field(value: Any, *, whole: bool = False) -> Any:
    """A value as the log keeps it: clipped unless it must stay whole (a request has to be
    rebuildable from its log), and with every held secret replaced either way. An agent
    pasted the server token into commands, and it sat in the log (2026-10-15)."""
    return redact_all(value if whole else _clip(value))
