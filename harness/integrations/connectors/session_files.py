"""Files that a connector tool carries out of the machine, and files it saves onto it.

A tool that uploads or attaches a local file is how data leaves the computer, so every one of them
asks the same questions (the rule `gmail_attach` set first):

  - only files inside the session's own folders, never through `..` or a link that resolves
    elsewhere;
  - never Reeve's own state (secrets, settings, the session database);
  - not in a remote session, where a path names the remote host's disk and this reads this one;
  - within the service's own size limit, said before the service refuses it.

A download lands in the session's first writable folder, under a name that never overwrites one
already there.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .mail.mime import safe_filename


def outgoing(raw: str, roots: Optional[list[Any]], *, remote: bool, limit: int, service: str) -> tuple[Path, str]:
    """(the file, "") or (path, why it may not leave)."""
    from harness.safety.boundary import is_protected_in_project, protected_paths

    if remote:
        return Path(), "sending a file is not available in a remote session: the path would be read from this computer"
    path = Path(raw).expanduser().resolve()
    inside = [Path(r.path).resolve() for r in (roots or [])]
    if not any(path.is_relative_to(root) for root in inside):
        return path, f"{raw} is outside the session's folders"
    if not path.is_file():
        return path, f"no file at {raw}"
    if any(path == p.resolve() for p in protected_paths()) or is_protected_in_project(path):
        return path, f"{raw} is part of Reeve's own state and is not sent"
    if path.stat().st_size > limit:
        return path, f"{raw} is larger than {service}'s {limit // (1024 * 1024)} MB limit"
    return path, ""


def landing(name: str, roots: Optional[list[Any]], *, remote: bool) -> tuple[Optional[Path], str]:
    """(where a download goes, "") or (None, why it cannot be saved)."""
    if remote:
        return None, "saving a file is not available in a remote session: it would be written to this computer, not the remote host"
    scratch = roots[0] if roots else None
    if scratch is None or not scratch.writable:
        return None, "no writable session folder to save the file into"
    target = Path(scratch.path) / safe_filename(name or "file")
    counter = 1
    while target.exists():
        stem = re.sub(r"-[0-9]+$", "", target.stem) or "file"
        target = Path(scratch.path) / f"{stem}-{counter}{target.suffix}"
        counter += 1
    return target, ""
