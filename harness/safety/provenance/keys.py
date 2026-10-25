"""One canonical key per file, so `./a.py`, `a.py` and the absolute path are the same entry."""

from __future__ import annotations

import posixpath
from pathlib import Path


def resolve(path: str, root: Path) -> str:
    """A local file's key. A relative path hangs off the workspace root; an absolute or `~` path is
    taken as it is. This follows how the permission engine resolves paths for its scoping."""
    given = Path(str(path)).expanduser()
    try:
        return str(given.resolve() if given.is_absolute() else (root / given).resolve())
    except (OSError, ValueError):  # a path that cannot be resolved at all
        return str(given)


def resolve_remote(path: str, root: str, home: str = "") -> str:
    """A remote file's key, with POSIX rules.

    The controller may run on Windows while a remote path is always `/`-rooted. A relative path
    hangs off the remote workspace, and `~` off the remote HOME once the probe has learned it. It
    never goes through the local `Path`, which would leak the controller's own path rules."""
    text = str(path or "").strip()
    if text == "~" and home:
        text = home
    elif text.startswith("~/") and home:
        text = posixpath.join(home, text[2:])
    if not posixpath.isabs(text):
        text = posixpath.join(root, text)
    return posixpath.normpath(text)
