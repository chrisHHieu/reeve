"""Cleaning outbound text so a request can always be encoded."""

from __future__ import annotations

import re
from typing import Any

#: Lone surrogates (U+D800-U+DFFF): strings Python can hold and UTF-8 cannot encode. Windows text
#: paths produce them (command output decoded with `errors="surrogateescape"`, a filename), and a
#: request carrying one fails at the transport with `UnicodeEncodeError`, after the turn has
#: already spent its context and time. Every provider client cleans at this one boundary.
_SURROGATES = re.compile(r"[\ud800-\udfff]")


def sanitize_text(text: str) -> str:
    """`text` with lone surrogates replaced by U+FFFD; the same object when it is clean."""
    return _SURROGATES.sub("�", text) if _SURROGATES.search(text) else text


def sanitize_messages(value: Any) -> Any:
    """A copy with every string cleaned. Containers are rebuilt and clean strings come back as they
    are. Providers apply it right before building the request."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_messages(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_messages(item) for key, item in value.items()}
    return value
