"""Re-establishing, from a resumed transcript, which files were read (`observed.py`).

Split from `observed.py` to keep it under the size ceiling: the store and its rule live there,
and this is the one reader of a transcript that feeds it.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .observed import READ_TOOLS, ObservedFiles


def seed_from_messages(observed: ObservedFiles, messages: Iterable[Any]) -> int:
    """Re-establish, from a resumed transcript, which files were read before this process.

    Without it every resumed session refuses its first edit: the model reads the file,
    the process restarts, and the store that witnessed the read is gone while the read
    itself is still in the model's context. That refusal would be true about this process
    and false about the conversation, which is the wrong of the two to answer.

    Seeded reads are UNVERIFIED, never re-hashed: hashing now would record what the file
    looks like TODAY as though the agent had seen it, and a file changed while the process
    was down would then pass the version guard it exists to fail. One real read (or one
    write) in this process upgrades the entry to a digest.

    Returns how many paths were seeded, so a caller can log a number rather than a guess.
    """
    seeded = 0
    for message in messages or ():
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            function = (call or {}).get("function") or {}
            argument = READ_TOOLS.get(function.get("name") or "")
            if argument is None:
                continue
            raw = _argument_of(function.get("arguments"), argument)
            if raw:
                observed.observe_unverified(raw)
                seeded += 1
    return seeded


def _argument_of(arguments: Any, name: str) -> str:
    """One argument out of a stored call, whose arguments are a JSON string on the wire."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except (TypeError, ValueError):
            return ""
    if not isinstance(arguments, dict):
        return ""
    value = arguments.get(name)
    return str(value) if value else ""


__all__ = ["seed_from_messages"]
