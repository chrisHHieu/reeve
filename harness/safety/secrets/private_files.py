"""Writing a file only its owner can read, without a window where others can."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from harness.platform.spawn import run as spawn_run

_IS_WINDOWS = sys.platform == "win32"


def restrict_to_user(path: Path, *, is_dir: bool) -> None:
    """Make `path` reachable by the current user alone.

    POSIX uses mode bits (0700 for a folder, 0600 for a file). Windows has none: `os.chmod` there
    only flips the read-only flag, so 0600 would do nothing and the file would keep broad inherited
    access (SYSTEM, Administrators). So an ACL is set instead: inheritance is removed and the
    current user alone is granted access. On Windows it is best effort, so a passing `icacls`
    failure never stops a key from being saved."""
    if _IS_WINDOWS:
        user = os.environ.get("USERNAME")
        if not user:
            return
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{user}" if domain else user
        # A folder's grant must be inheritable, (OI) for files and (CI) for subfolders, so what is
        # created inside (the SQLite stores, the conversations) inherits the user's access. Without
        # the flags, removing inheritance leaves a non-inheritable entry on the folder and every
        # child gets an empty ACL: SQLite then fails with "unable to open database file" and the
        # server crashes on launch.
        grant = f"{account}:(OI)(CI)F" if is_dir else f"{account}:F"
        try:
            spawn_run(["icacls", str(path), "/inheritance:r", "/grant:r", grant], capture_output=True, check=False)
        except OSError:
            pass
        return
    os.chmod(path, 0o700 if is_dir else 0o600)


def _replace_through_transient(temp: Path, target: Path, attempts: int = 5) -> None:
    """`os.replace`, retried across the short Windows refusals.

    Windows will not rename over a file another process holds open, and a scanner can be holding
    the new temp file. A real session lost a calendar call to `[WinError 5] Access is denied`
    on exactly this rename. Both holders let go quickly, so a few short waits turn a failed write
    into a finished one, and a real permission problem still raises on the last attempt."""
    wait = 0.02
    for attempt in range(attempts):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)
            wait *= 2


def atomic_private_write(target: Path, content: str) -> Path:
    """Write `content` to `target` in one step, never through a temp file others can read.

    A temp file made by `Path.write_text` and restricted afterwards sat on disk with the default
    umask (0644 on a normal machine) while it was written, readable by every local process and by
    whatever backed the folder up. `tempfile.mkstemp` creates the file 0600, exclusively, before a
    byte goes in, and its random name replaces a fixed `<name>.tmp` that a local attacker could
    have pre-created as a symlink to steer the write elsewhere.

    mkstemp gives Windows no mode bits, so the ACL is applied to the still-empty file first."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        restrict_to_user(target.parent, is_dir=True)
    except OSError:
        pass

    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        restrict_to_user(temp, is_dir=False)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(content)
        _replace_through_transient(temp, target)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    return target


def write_private_text(path: str | Path, content: str) -> Path:
    """Write a text file only the current user can read, with the secret store's protections."""
    return atomic_private_write(Path(path).expanduser(), content)
