"""Finding a command-line tool on this machine, and describing one Reeve could install.

Every answer is an absolute path. Once a tool is resolved it is invoked by that path and never by
PATH again, so it works even when the caller's environment is bare.

The desktop shell hands over the login shell's PATH at spawn. `resolve` covers every other way of
launching (headless, systemd, a double-clicked binary), and also looks in the folders launchd's
PATH never includes.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from . import catalog
from . import paths
from .paths import managed_path

# Folders that hold user-installed tools but are missing from launchd's PATH. They mirror
# KNOWN_TOOL_DIRS in the desktop shell (surfaces/gui/electron/shellEnv.ts), so change the two together.
KNOWN_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/opt/local/bin",
    "~/.local/bin",
    "~/.cargo/bin",
    "~/go/bin",
)


def is_runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve(name: str) -> Optional[str]:
    """The absolute path of `name`, or None.

    PATH is searched first, because the user's choice wins. Then the folders a GUI launch cannot
    see, then anything Reeve installed itself."""
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())

    # The listed folders are macOS and Linux ones, but a Windows entry, or a Windows tool inside a
    # mapped folder, is an `.exe`. Probing the platform's suffix as well is what keeps such a hit
    # from reading as missing.
    suffixes = ["", ".exe"] if sys.platform == "win32" else [""]
    for folder in KNOWN_DIRS:
        for suffix in suffixes:
            candidate = Path(folder).expanduser() / f"{name}{suffix}"
            if is_runnable(candidate):
                return str(candidate.resolve())

    tool = catalog.MANAGED.get(name)
    if tool:
        ours = managed_path(tool)
        if is_runnable(ours):
            return str(ours)
    return None


def have(name: str) -> bool:
    return resolve(name) is not None


def missing(names: Iterable[str]) -> list[str]:
    """The names that cannot be found. A skill asks this before it promises a scan."""
    return [name for name in names if not have(name)]


def installable(name: str) -> bool:
    """Whether Reeve could install this itself, which is whether it is pinned for this platform."""
    tool = catalog.MANAGED.get(name)
    return bool(tool and paths.platform_key() in tool.downloads)


def describe(name: str) -> Optional[dict[str, str]]:
    """What the consent card shows when asking permission to install a tool."""
    tool = catalog.MANAGED.get(name)
    if not tool:
        return None
    download = tool.downloads.get(paths.platform_key())
    if not download:
        return None
    parsed = urlparse(download.url)
    segments = [part for part in parsed.path.split("/") if part]
    return {
        "name": tool.name,
        "version": tool.version,
        "summary": tool.summary,
        "url": download.url,
        "sha256": download.sha256,
        # The publisher in a readable form, such as "github.com/aquasecurity".
        "source": parsed.netloc + (f"/{segments[0]}" if segments else ""),
    }
