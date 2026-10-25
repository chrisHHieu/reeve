"""Reading a message: encoded headers, the text of the body, and its attachments."""

from __future__ import annotations

import email as email_lib
import re
from email.header import decode_header
from pathlib import Path
from typing import Any

BODY_CHAR_LIMIT = 20_000


def decode_mime_header(raw: Any) -> str:
    """A header with its encoded words decoded. A charset label nobody has heard of falls back
    to UTF-8 instead of failing the message."""
    if not raw:
        return ""
    pieces = []
    for part, charset in decode_header(str(raw)):
        if isinstance(part, bytes):
            try:
                pieces.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                pieces.append(part.decode("utf-8", errors="replace"))
        else:
            pieces.append(part)
    return "".join(pieces)


_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"))


def _html_to_text(html: str) -> str:
    text = re.sub(r"<(br|/p|/div|/tr)\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _part_text(part: email_lib.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_text_body(msg: email_lib.message.Message) -> str:
    """The best text rendering of a message: the first plain part, else the first HTML part
    with its tags removed, cut at `BODY_CHAR_LIMIT`."""
    plain = html = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        kind = part.get_content_type()
        if kind == "text/plain" and not plain:
            plain = _part_text(part)
        elif kind == "text/html" and not html:
            html = _part_text(part)
    text = plain or _html_to_text(html)
    if len(text) > BODY_CHAR_LIMIT:
        text = text[:BODY_CHAR_LIMIT] + "\n…[truncated]"
    return text


def list_attachment_parts(msg: email_lib.message.Message) -> list[tuple[str, email_lib.message.Message]]:
    """(filename, part) for every attachment, including named inline parts."""
    found = []
    if not msg.is_multipart():
        return found
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        if "attachment" not in disposition and not (filename and "inline" in disposition):
            continue
        if filename:
            found.append((decode_mime_header(filename), part))
    return found


def safe_filename(name: str) -> str:
    """A name that cannot leave the folder it is saved in or upset Windows: any path a sender
    chose is dropped, the characters Windows refuses are replaced, and it is never empty."""
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(". ")
    return name or "attachment"
