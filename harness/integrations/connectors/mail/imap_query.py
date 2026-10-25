"""IMAP commands: building a search, reading a folder listing, opening and fetching read-only.

Every read is non-destructive. Folders are opened read-only and messages fetched with `PEEK`,
so a message the person has not read stays unread.
"""

from __future__ import annotations

import email as email_lib
import imaplib
import re
from typing import Optional

_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$')


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_date(value: str) -> Optional[str]:
    found = _DATE.match(value.strip())
    if not found:
        return None
    year, month, day = (int(group) for group in found.groups())
    if not 1 <= month <= 12:
        return None
    return f"{day:02d}-{_MONTHS[month - 1]}-{year}"


def build_search_criteria(
    *,
    from_address: str = "",
    to_address: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unread_only: bool = False,
) -> tuple[Optional[bytes], str]:
    """(an IMAP SEARCH string as bytes, "") or (None, why the input is invalid)."""
    terms: list[str] = []
    for key, value in (("FROM", from_address), ("TO", to_address), ("SUBJECT", subject), ("TEXT", text)):
        if value and value.strip():
            terms.append(f"{key} {quote(value.strip())}")
    for key, value in (("SINCE", since), ("BEFORE", before)):
        if value and value.strip():
            date = _imap_date(value)
            if date is None:
                return None, f"invalid {key.lower()} date {value!r}; use YYYY-MM-DD"
            terms.append(f"{key} {date}")
    if unread_only:
        terms.append("UNSEEN")
    criteria = " ".join(terms) or "ALL"
    if criteria.isascii():
        return criteria.encode("ascii"), ""
    # Terms outside ASCII go as UTF-8 with an explicit CHARSET, which Gmail and iCloud accept.
    return b"CHARSET UTF-8 " + criteria.encode("utf-8"), ""


def parse_list_line(line: bytes) -> Optional[str]:
    """The folder name on a LIST response line, or None for a line that is not a selectable
    folder."""
    found = _LIST_LINE.match(line)
    if not found:
        return None
    name = found.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = name[1:-1].replace(b'\\"', b'"')
    if rb"\Noselect" in found.group("flags"):
        return None
    try:
        return name.decode("utf-8")
    except UnicodeDecodeError:
        return name.decode("latin-1")


def select_readonly(imap: imaplib.IMAP4, folder: str) -> Optional[str]:
    """None when the folder opened, else why not."""
    status, _ = imap.select(quote(folder), readonly=True)
    return None if status == "OK" else f"cannot open folder {folder!r}"


def fetch_message(imap: imaplib.IMAP4, uid: str) -> Optional[email_lib.message.Message]:
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return None
    return email_lib.message_from_bytes(data[0][1])
