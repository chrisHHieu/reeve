"""The server's own log: what it did off the turn.

Titles, review assists, scheduled runs and connector relays happen away from any turn. With no
logging configured every `logger.info` in the package was dropped, since the root default is
WARNING, and a review could not tell whether such work had ever run. INFO goes to a
rotating file that outlives a launch; WARNING also reaches stderr, which the desktop shell
keeps as its server log.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from harness.safety.secrets import state_dir

LOG_FILE = "reeve.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 3


def configure_logging(directory: Optional[Path] = None) -> Optional[Path]:
    """Attach the file and stderr handlers to the package logger, once, and return the log's
    path. Best effort: a log that cannot be opened costs the log and never the server."""
    package = logging.getLogger("harness")
    if getattr(package, "_reeve_configured", False):
        return None
    level = os.environ.get("REEVE_LOG_LEVEL", "INFO").upper()
    package.setLevel(getattr(logging, level, logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(formatter)
    package.addHandler(stderr)
    package._reeve_configured = True  # type: ignore[attr-defined]
    try:
        folder = directory or (state_dir() / "logs")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / LOG_FILE
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(formatter)
    package.addHandler(handler)
    return path
