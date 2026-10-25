"""The session's environment, written into the system prompt when the engine is built.

It saves the agent three or four discovery calls every session (pwd, uname, git status, git log)
by saying up front where it is and what state the workspace is in. The git snapshot is a moment in
time, and the prompt says so, so the agent checks again before relying on it.
"""

from __future__ import annotations

import platform as _platform
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from harness.platform.spawn import run as spawn_run

_STATUS_LINES = 20
_GIT_TIMEOUT_SECONDS = 5


def _git(workspace: Path, *args: str) -> Optional[str]:
    """What one git command in `workspace` printed; None when it failed or there is no git."""
    command = ["git", "-C", str(workspace), *args]
    try:
        finished = spawn_run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return finished.stdout.strip() if finished.returncode == 0 else None


def _status_line(porcelain: str) -> str:
    changed = porcelain.splitlines()
    if not changed:
        return "Git status: clean"
    shown = "\n".join(changed[:_STATUS_LINES])
    more = f"\n… and {len(changed) - _STATUS_LINES} more" if len(changed) > _STATUS_LINES else ""
    return f"Git status ({len(changed)} changed):\n{shown}{more}"


def _git_snapshot(workspace: Path) -> list[str]:
    """Branch, changed files and the last five commits, or one line saying it is no repository."""
    if _git(workspace, "rev-parse", "--is-inside-work-tree") != "true":
        return ["Git: not a git repository"]
    branch = _git(workspace, "rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
    status = _git(workspace, "status", "--porcelain")
    recent = _git(workspace, "log", "-n5", "--pretty=format:%h %s")
    return [
        f"Git branch: {branch}",
        *([_status_line(status)] if status is not None else []),
        *([f"Recent commits:\n{recent}"] if recent else []),
    ]


def os_name() -> str:
    """The OS as a person would name it.

    `platform.release()` answers "10" on Windows 11, since the kernel is still NT 10.0, so the
    prompt once told the model it was on Windows 10 (2026-09-24 audit). Windows 11 is NT 10.0 build
    22000 and later, and `platform.version()` carries the build.
    """
    mac = _platform.mac_ver()[0]
    if mac:
        return f"macOS {mac}"
    system, release = _platform.system(), _platform.release()
    if system == "Windows":
        fields = _platform.version().split(".")
        build = int(fields[2]) if len(fields) >= 3 and fields[2].isdigit() else 0
        if release == "10" and build >= 22000:
            return f"Windows 11 (build {build})"
        if build:
            return f"Windows {release} (build {build})"
    return f"{system} {release}"


def environment_context(workspace: str | Path) -> str:
    """The system-prompt block describing the session's environment and git state."""
    folder = Path(workspace).expanduser().resolve()
    body = "\n".join(
        [
            f"Workspace: {folder}",
            f"Platform: {sys.platform} ({os_name()})",
            f"Today's date: {date.today().isoformat()}",
            *_git_snapshot(folder),
        ]
    )
    # The reason a sweep hurts is macOS's (each touch is a permission prompt the user cannot place),
    # so it is said only there; the rule holds everywhere.
    why = (
        " On macOS every such touch fires an OS permission prompt the user can't connect to any "
        "action they took."
        if sys.platform == "darwin"
        else ""
    )
    return (
        "Environment (as it was when the session started; check git again before you rely on "
        f"it):\n<environment>\n{body}\n</environment>\n"
        "Where to work: in the workspace and in folders the user has given you. Do not read or "
        "list anywhere else (sweeps of the home folder, ~/Desktop, ~/Downloads, photo libraries "
        f"and the like), and not with find, ls or grep in the shell either.{why} "
        "When the task needs files in another folder, such as a project the user named, use the "
        "file tools on it directly: once that is allowed, the folder joins the session."
    )
