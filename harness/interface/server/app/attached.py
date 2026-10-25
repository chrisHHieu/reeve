"""What a person attached to a message, as the transcript draws it.

The model reads attachments as content parts: an image as a picture, a PDF as a file part, text
inline or as a head and a path, any other file as a line naming where it was saved. None of that
says, once the conversation is reopened, that a message carried `Q3-report.pdf` (2.4 MB): a PDF
part has no size, a text file is a block of the message's own text, and a picture has no name.

So the message keeps one row per attachment beside its content, in the `_attached` sidecar, never
sent to a provider (`llm/wire.py`): its kind, name, type, size, and for text its line count. Each
file that is not a picture is also kept in the session's `attachments` folder, a root the panel can
open, and its row names the path: a card in the message opens the file itself. A picture needs no
copy: its bytes are in the content and the transcript draws them from there.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable, Optional

#: Longest name a row keeps; a name past it is cut (a file name is not a place for a novel).
MAX_NAME = 200


class KeptFiles:
    """Saves each attachment's bytes once. The content builder saves a binary file it cannot send
    natively (`save_file` in `llm/attachments.py`); handing it this saver means the copy the row
    names and the copy the model is told about are the same file."""

    def __init__(self, save: Callable[[str, bytes], Optional[str]]):
        self._save = save
        self._paths: dict[tuple[str, int, str], Optional[str]] = {}

    def save(self, name: str, data: bytes) -> Optional[str]:
        key = (name, len(data), hashlib.sha1(data).hexdigest())
        if key not in self._paths:
            self._paths[key] = self._save(name, data)
        return self._paths[key]


def _bytes_of(item: dict) -> Optional[bytes]:
    if item.get("kind") == "text":
        return str(item.get("text") or "").encode("utf-8")
    url = item.get("data_url")
    if not isinstance(url, str) or ";base64," not in url:
        return None
    try:
        return base64.b64decode(url.partition(",")[2])
    except Exception:
        return None


def _lines(body: str) -> int:
    if not body:
        return 0
    return body.count("\n") + (0 if body.endswith("\n") else 1)


def attached_rows(attachments: Any, kept: Optional[KeptFiles]) -> list[dict[str, Any]]:
    """One row per attachment, in the order sent. Best effort: an attachment that cannot be read
    still gets its name and kind, and a copy that cannot be saved leaves the row without a path."""
    rows: list[dict[str, Any]] = []
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") if item.get("kind") in ("image", "pdf", "text", "file") else "file"
        name = str(item.get("name") or "attachment")[:MAX_NAME]
        row: dict[str, Any] = {"kind": kind, "name": name}
        mime = item.get("mime")
        if isinstance(mime, str) and mime:
            row["mime"] = mime[:100]
        data = _bytes_of(item)
        if data is not None:
            row["size"] = len(data)
            if kind == "text":
                row["lines"] = _lines(str(item.get("text") or ""))
            if kind != "image" and kept is not None:
                try:
                    path = kept.save(name, data)
                except Exception:
                    path = None
                if path:
                    row["path"] = path
        rows.append(row)
    return rows
