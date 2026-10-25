"""Files on an outgoing Gmail message.

Reading a local file and mailing it is the one Gmail tool that can carry data OUT of the machine,
so the rules are stricter than for a read tool:

  - only files inside the session's own directories (the same rule the IMAP `email_send` uses),
    never through `..` or a link that resolves elsewhere;
  - never Reeve's own state (secrets, settings, the session database): those are the floors
    `boundary.protected_paths` keeps the agent from writing, and they are no safer to mail;
  - not in a remote session, where a path names the remote host's disk and this reads this one;
  - bounded: Gmail refuses a message over 25 MB once encoded, so the total is capped below that
    and the refusal says so instead of failing at Google.

The paths ride in the call's arguments, so the approval card lists exactly which files leave.
"""

from __future__ import annotations

import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable, Optional

#: Bytes of attachments per message. Base64 adds a third, and Gmail's ceiling is 25 MB.
MAX_TOTAL = 18 * 1024 * 1024


def _protected(path: Path) -> bool:
    from harness.safety.boundary import is_protected_in_project, protected_paths

    resolved = path.resolve()
    return any(resolved == p.resolve() for p in protected_paths()) or is_protected_in_project(resolved)


def _add(msg: EmailMessage, name: str, data: bytes, mime: str = "") -> None:
    maintype, _, subtype = (mime or mimetypes.guess_type(name)[0] or "application/octet-stream").partition("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)


def attach_paths(
    msg: EmailMessage, paths: Optional[Iterable[str]], roots: Optional[list[Any]], *, remote: bool, used: int = 0
) -> tuple[Optional[str], int]:
    """(error, bytes attached). Adds each file to `msg`, or names the first one it refuses."""
    wanted = [str(p) for p in (paths or []) if str(p).strip()]
    if not wanted:
        return None, used
    if remote:
        return (
            "attachments are not available in a remote session: the path would be read from "
            "this computer, not the remote host.",
            used,
        )
    inside = [Path(r.path).resolve() for r in (roots or [])]
    total = used
    for raw in wanted:
        path = Path(raw).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in inside):
            return f"attachment {raw} is outside the session's directories", total
        if not path.is_file():
            return f"attachment not found: {raw}", total
        if _protected(path):
            return f"attachment {raw} is part of Reeve's own state and is not sent", total
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL:
            return f"attachments total more than {MAX_TOTAL // (1024 * 1024)} MB, which Gmail would refuse", total
        _add(msg, path.name, data)
    return None, total


def attach_bytes(msg: EmailMessage, name: str, data: bytes, mime: str, used: int) -> tuple[Optional[str], int]:
    """A file that came from another message (a forward carries the original's attachments)."""
    if used + len(data) > MAX_TOTAL:
        return f"the forwarded attachments total more than {MAX_TOTAL // (1024 * 1024)} MB; forward without them", used
    _add(msg, name, data, mime)
    return None, used + len(data)
