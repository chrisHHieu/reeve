"""The secret values this process holds, so none of them is written into a log or shown to a model.

`safety/redaction.py` finds credentials by their shape (a provider prefix, a labeled assignment).
A credential with no shape slips past it: the local server's own token is 64 hex characters, which
a git sha also is. An agent working on Reeve read that token from its file and pasted it into
`curl` commands, so it sat in the session log and the transcript (2026-10-15). The harness knows
these values exactly, so it removes exactly them: the server token from the environment, and every
value the secret store has handed out or been given in this process.

A shell started by the server inherits `REEVE_API_TOKEN`, so a command that needs the token names
the variable, and the redaction says so.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

#: Shorter values are too likely to occur in ordinary text to be replaced safely.
MIN_LENGTH = 12
SERVER_TOKEN = "REEVE_API_TOKEN"
REDACTED = "[REDACTED]"
REDACTED_SERVER_TOKEN = "[REDACTED: the server token; a shell has it as $REEVE_API_TOKEN]"

_lock = threading.Lock()
_held: set[str] = set()


#: The names a credential is kept under. A profile also holds a base URL, an account name or a
#: region, and replacing those everywhere they appear would mangle ordinary text.
_SECRET_KEY = re.compile(r"key|token|secret|passw|credential|auth|bearer|cookie|private", re.IGNORECASE)


def remember(value: Any, *, key: str = "") -> None:
    """Hold the credentials in `value` (a profile dict, a list, a string): the strings kept under a
    credential's name, long enough to be replaced safely. A bare string is held as one."""
    found: list[str] = []

    def walk(item: Any, name: str, bare: bool) -> None:
        if isinstance(item, str):
            if (bare or _SECRET_KEY.search(name)) and len(item) >= MIN_LENGTH and not item.isspace():
                found.append(item)
        elif isinstance(item, dict):
            for k, v in item.items():
                walk(v, str(k), False)
        elif isinstance(item, (list, tuple)):
            for v in item:
                walk(v, name, bare)

    walk(value, key, isinstance(value, str))
    if found:
        with _lock:
            _held.update(found)


def redact(text: str) -> str:
    """`text` with every held secret, and the server token, replaced. Never raises."""
    if not text or not isinstance(text, str):
        return text
    try:
        token = os.environ.get(SERVER_TOKEN) or ""
        if len(token) >= MIN_LENGTH and token in text:
            text = text.replace(token, REDACTED_SERVER_TOKEN)
        with _lock:
            held = sorted((s for s in _held if s in text), key=len, reverse=True)
        for secret in held:
            text = text.replace(secret, REDACTED)
        return text
    except Exception:  # noqa: BLE001 - a redaction that fails must not cost the write it guards
        return text


def redact_all(value: Any) -> Any:
    """`redact` over every string inside a value made of dicts, lists and strings."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_all(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_all(v) for v in value]
    return value


def forget_all() -> None:
    """For tests: hold nothing."""
    with _lock:
        _held.clear()
