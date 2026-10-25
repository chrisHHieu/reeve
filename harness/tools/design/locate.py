"""Where the harness's browser is: the Chromium already on the machine.

Edge is on every Windows install; else Chrome or Chromium; else a Chromium the Playwright cache
holds, which the installer's browser MCP brings. Nothing is installed for a design check.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

#: Set to a browser executable to use that one (a person whose Chromium is somewhere unusual).
BROWSER_ENV = "REEVE_DESIGN_BROWSER"


def _candidates() -> list[Path]:
    """Where a Chromium usually is on this OS, most likely first."""
    found: list[Path] = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            found.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            found.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
            found.append(Path(base) / "Chromium" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"):
            found.append(Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / app)
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            located = shutil.which(name)
            if located:
                found.append(Path(located))
    return found


def _playwright_chromium() -> list[Path]:
    """A Chromium the Playwright cache holds, newest first: the installer's browser MCP brings one."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
        tail = ("chrome-win", "chrome.exe")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
        tail = ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium")
    else:
        base = Path.home() / ".cache" / "ms-playwright"
        tail = ("chrome-linux", "chrome")
    if not base.is_dir():
        return []
    folders = sorted((p for p in base.glob("chromium-*") if p.is_dir()), key=lambda p: p.name, reverse=True)
    return [folder.joinpath(*tail) for folder in folders]


def find_browser() -> Optional[Path]:
    """The Chromium a design check runs in, or None when this machine has none."""
    override = os.environ.get(BROWSER_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    for path in _candidates() + _playwright_chromium():
        if path.is_file():
            return path
    return None
