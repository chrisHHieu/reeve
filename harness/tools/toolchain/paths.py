"""Where managed tools live on disk, and what this machine is called in the release assets."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from harness.safety.secrets import state_dir

from . import catalog


def managed_dir() -> Path:
    """Where tools installed by Reeve itself are kept, apart from the user's own copies."""
    return state_dir() / "tools"


def bin_dir() -> Path:
    """One stable folder of links to the current pinned binaries.

    The binaries sit in versioned folders. This is the one that goes on a shell's PATH, so a tool
    installed mid-session is seen by a shell that is already running, with no respawn."""
    return managed_dir() / "bin"


def platform_key() -> str:
    """`<os>_<arch>`, spelled the way upstream release assets spell it."""
    system = {"darwin": "darwin", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"{system}_{arch}"


def managed_path(tool: catalog.ManagedTool) -> Path:
    exe = tool.name + (".exe" if sys.platform == "win32" else "")
    return managed_dir() / tool.name / tool.version / exe
