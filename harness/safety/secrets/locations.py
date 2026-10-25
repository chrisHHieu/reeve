"""Where Reeve keeps its state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The session database's file name under the state folder.
DATABASE = "reeve.db"


def database_path(base: Path) -> Path:
    """Where the session database lives under `base`."""
    return base / DATABASE


def protected_database_names() -> tuple[str, ...]:
    """The session database's file names, for a guard that must cover it."""
    return (DATABASE,)


def state_dir() -> Path:
    """The folder holding Reeve's state: one answer on every platform.

    In order: `$REEVE_STATE_DIR` on any OS (tests and sidecars use it); on Windows
    `%APPDATA%\\Reeve`, the per-user app-data folder; elsewhere `~/.config/Reeve`."""
    override = os.environ.get("REEVE_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Reeve"
    return Path.home() / ".config" / "Reeve"
