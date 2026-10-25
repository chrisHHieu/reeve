"""Exiting when the desktop shell that launched the server is gone.

When started as a sidecar (`REEVE_EXIT_WITH_PARENT=1`) the server must not outlive its
parent, even if the parent was killed abruptly (the dev watcher restarting the app, a crash)
and never got to stop its children. A standalone `reeve-server` run is unaffected.

The shell passes its own PID in `REEVE_PARENT_PID`. Watching that explicit PID, and not
`getppid`, is what keeps this working under a one-file PyInstaller build, where this process is
a grandchild of the shell: the bootloader sits between them, `getppid` points at it, and a
re-parenting check never fires when the shell dies. That is how a pair of servers once leaked
on every quit.
"""

from __future__ import annotations

import os
import sys
import threading
import time


def exit_when_orphaned() -> None:
    if os.environ.get("REEVE_EXIT_WITH_PARENT") != "1":
        return
    try:
        parent = int(os.environ.get("REEVE_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    parent = parent or os.getppid()  # a standalone run falls back to its direct spawner
    if sys.platform == "win32":
        _watch_windows(parent)
    else:
        _watch_posix(parent)


def _watch_posix(parent: int) -> None:
    """Poll the PID with signal 0, which delivers nothing and only checks the process exists."""
    original_ppid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.5)
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # alive but owned by someone else, which should not happen: keep waiting
            # A second signal for PID-reuse edge cases: our direct parent changed.
            if os.getppid() != original_ppid:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _watch_windows(parent: int) -> None:
    """Block on a handle to the parent and exit only when it actually terminates.

    Best effort: if anything fails, the shell's own exit handler remains the primary cleanup.
    Two details bit before. `OpenProcess` returns a 64-bit HANDLE and ctypes defaults the return
    type to a 32-bit int, which truncates it to garbage, so the types are declared. And only
    WAIT_OBJECT_0 means the parent died: a bad handle returns WAIT_FAILED at once, and treating
    that as "died" killed a perfectly healthy server seconds after startup.
    """
    import ctypes
    from ctypes import wintypes

    synchronize = 0x0010_0000
    infinite = 0xFFFF_FFFF
    wait_object_0 = 0x0000_0000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    handle = kernel32.OpenProcess(synchronize, False, parent)
    if not handle:
        return

    def watch() -> None:
        if kernel32.WaitForSingleObject(handle, infinite) == wait_object_0:
            os._exit(0)

    threading.Thread(target=watch, daemon=True).start()
