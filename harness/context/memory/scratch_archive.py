"""Park the per-conversation scratch memories.

Before every "no project" conversation shared one key (`general`), each
scratch folder `<scratch_base>/<session_id>` keyed its own memory directory: 14 of them on
the owner's install (2026-09-28), ~210 files no later session can read, each tidied by
nobody. They are moved whole into `projects/.archive/` — not merged into `general`, since
most are one conversation's working notes, and not deleted, since a person may still want
one. Moving is idempotent (a parked directory is no longer found), so there is no marker.
"""

from __future__ import annotations

import re
from pathlib import Path

ARCHIVE_DIR = ".archive"

#: A session id right after the scratch prefix: `uuid4().hex[:12]`, the older
#: `str(uuid4())[:12]`, or a whole uuid — then the end or a sub-folder (`…-cv`).
_SESSION_ID = re.compile(
    r"(?:[0-9a-f]{12}|[0-9a-f]{8}-[0-9a-f]{3}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:-|$)"
)


def archive_scratch_memories(projects: Path, scratch_prefix: str) -> list[str]:
    """Move every `<scratch_prefix>-<session id>…` directory under `projects` into
    `projects/.archive/`; returns the names moved. `scratch_prefix` is the scratch base's
    own directory name (the key → directory rule applied to it). A directory that cannot
    move (a file open on Windows, a name already parked) stays for the next start."""
    prefix = scratch_prefix.strip("-")
    if not prefix or not projects.is_dir():
        return []
    archive = projects / ARCHIVE_DIR
    moved: list[str] = []
    for d in sorted(projects.iterdir()):
        if not d.is_dir() or not d.name.startswith(prefix + "-"):
            continue
        if not _SESSION_ID.match(d.name[len(prefix) + 1 :]):
            continue
        target = archive / d.name
        if target.exists():
            continue
        try:
            archive.mkdir(exist_ok=True)
            d.rename(target)
        except OSError:
            continue
        moved.append(d.name)
    return moved
