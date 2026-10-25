"""Move a file or folder to the system's trash, where the person can restore it.

The file manager's Delete is the person's own action (the agent has no route to it), and it is
never a permanent delete: Windows gets the Recycle Bin (`SHFileOperationW` with undo allowed,
the call Explorer makes), macOS the user's `~/.Trash`, and Linux the freedesktop trash
(`$XDG_DATA_HOME/Trash`, with the `.trashinfo` that lets a file manager put it back).
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote


def _free_name(folder: Path, name: str) -> Path:
    """`name` in `folder`, or `name 2`, `name 3`, … when it is taken."""
    candidate = folder / name
    stem, suffix = os.path.splitext(name)
    n = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = folder / f"{stem} {n}{suffix}"
        n += 1
    return candidate


def _recycle_windows(path: Path) -> Optional[str]:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    fo_delete = 3
    # Undo allowed (the Recycle Bin), and no dialog of the shell's own: the GUI asked already.
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    op = SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=fo_delete,
        pFrom=str(path) + "\0",  # the list is double-null terminated; ctypes adds the second
        pTo=None,
        fFlags=flags,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        return f"the Recycle Bin refused it (code {result})"
    return None


def _trash_mac(path: Path) -> Optional[str]:
    folder = Path.home() / ".Trash"
    folder.mkdir(exist_ok=True)
    shutil.move(str(path), str(_free_name(folder, path.name)))
    return None


def _trash_freedesktop(path: Path) -> Optional[str]:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    files, info = base / "files", base / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    target = _free_name(files, path.name)
    (info / f"{target.name}.trashinfo").write_text(
        "[Trash Info]\n"
        f"Path={quote(str(path.resolve()))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )
    shutil.move(str(path), str(target))
    return None


def move_to_trash(path: Path) -> Optional[str]:
    """Move `path` to the trash; the error, or None when it is there."""
    try:
        if sys.platform == "win32":
            return _recycle_windows(path)
        if sys.platform == "darwin":
            return _trash_mac(path)
        return _trash_freedesktop(path)
    except OSError as exc:
        return str(exc)
