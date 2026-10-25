"""Whether the turn a memory is written in read content from outside the machine.

A memory is replayed into later sessions, so text that came from a web page or a connector can
travel further through one than through the turn that read it. Codex keeps such sessions out of
memory altogether; here the note is saved and its record says where it was written, so the index
line a later session sees carries "check it against the source".

Read from the thread, not kept as a flag: a resumed session answers the same as the one that ran.
"""

from __future__ import annotations

from typing import Any, Sequence

from harness.safety.risk import EGRESS_TOOLS
from harness.session.turns import is_user_turn

#: A connector's tools (`mcp/tools.py` names them `mcp__<server>__<tool>`) and the browser.
_OUTSIDE_PREFIXES = ("mcp__", "browser_")


def _names(message: dict[str, Any]) -> list[str]:
    out = []
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            out.append(str(function.get("name") or call.get("name") or ""))
    return out


def read_outside_content(messages: Sequence[dict[str, Any]]) -> bool:
    """True when the current turn (everything after the person's last message) called a web
    tool, the browser or a connector's tool."""
    for message in reversed(list(messages or [])):
        if is_user_turn(message):
            return False
        if message.get("role") != "assistant":
            continue
        for name in _names(message):
            if name in EGRESS_TOOLS or name.startswith(_OUTSIDE_PREFIXES):
                return True
    return False


__all__ = ["read_outside_content"]
