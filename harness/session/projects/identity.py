"""Which project a directory belongs to, and how to show that to a person.

The ladder, from the agent-teams design: an explicit binding on the session beats derivation.
Derivation prefers the git repository, so every worktree of one repo collapses to one project, and a
plain folder falls back to its resolved path, which is the key it always had. Resolution happens at
discrete moments (engine build, root grant) and nothing watches the filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run


def _git_common_dir(workspace: Path) -> Optional[Path]:
    try:
        done = spawn_run(
            ["git", "-C", str(workspace), "rev-parse", "--git-common-dir"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = done.stdout.strip() if done.returncode == 0 else ""
    if not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = workspace / common
    try:
        return common.resolve()
    except OSError:
        return None


def project_key(workspace: str | Path) -> str:
    """The project key of a directory: the repository folder for a git checkout, which every worktree
    shares, and otherwise the resolved path. It never raises, and a missing directory resolves as a
    path."""
    folder = Path(workspace).expanduser()
    try:
        folder = folder.resolve()
    except OSError:
        folder = Path(str(workspace))
    if folder.is_dir():
        common = _git_common_dir(folder)
        if common is not None:
            # The usual layout is <repo>/.git, whose key is <repo>. A bare or unusual layout keeps the
            # common directory itself, which is still one stable key per repository.
            return str(common.parent if common.name == ".git" else common)
    return str(folder)


def project_label(key: str, *, home: Optional[str] = None) -> dict[str, Any]:
    """How a derived key is shown. The kind is `git` when the key currently looks like a repository,
    that is when it has a `.git`. A git label is the repo folder's name, and a folder label is its
    path with the home folder collapsed to `~` (the GUI trims it to the last three segments)."""
    path = Path(key)
    is_git = (path / ".git").exists()
    shown = key
    prefix = (home or str(Path.home())).rstrip("/\\")
    # Both separators are accepted, because the key can come from either kind of string. The collapse
    # once compared against `home + "/"`, which never matched on Windows, where `str(Path)` uses
    # backslashes, so every folder project showed its full absolute path and not `~/…`.
    if shown.rstrip("/\\") == prefix:
        shown = "~"
    elif shown.startswith(prefix + "/") or shown.startswith(prefix + "\\"):
        shown = "~" + shown[len(prefix):]
    return {
        "kind": "git" if is_git else "folder",
        # Forward slashes on every platform, so a Windows label reads like the documented `~/x/y`
        # and whatever trims it can split one way. `full` keeps the real path.
        "label": path.name if is_git else shown.replace("\\", "/"),
        "full": key,
    }
