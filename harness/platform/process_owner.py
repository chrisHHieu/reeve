"""Which process left a record, and whether that process is still running.

A record in the state folder can be read by more than one process: the desktop app and a dev
server share `%APPDATA%\\Reeve`, and an agent working on Reeve can start a second server beside the
one it runs in. A record that only says "a turn was running" was read by the next process to
start as "the process that ran it is gone", and that process picked up a turn another live
process was still running (two engines on one session, 2026-10-15). A record that names its owner
lets the reader tell a leftover from a neighbour's work.

The pid alone does not name a process, because pids are reused, so the owner is the pid and the
time the process started. Where the start time cannot be read the pid decides, which is the
weaker answer and never a crash: like every platform seam here, this is best-effort.
"""

from __future__ import annotations

import os
from typing import Any, Optional

_IS_WINDOWS = os.name == "nt"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _windows_state(pid: int) -> tuple[bool, Optional[int]]:
    """(running, start time in 100 ns ticks) for a Windows pid."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False, None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return False, None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return True, None
        return True, (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def _posix_state(pid: int) -> tuple[bool, Optional[int]]:
    """(running, start time in clock ticks since boot) for a POSIX pid; Linux has the start."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        pass  # it exists, owned by someone else
    except OSError:
        return False, None
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return True, int(fields[19])  # field 22 of the line, after pid and the (comm)
    except (OSError, IndexError, ValueError):
        return True, None


def _state(pid: int) -> tuple[bool, Optional[int]]:
    if pid <= 0:
        return False, None
    try:
        return _windows_state(pid) if _IS_WINDOWS else _posix_state(pid)
    except Exception:  # noqa: BLE001 - a seam that cannot answer says "not running"
        return False, None


def current() -> dict[str, Any]:
    """This process, as a record names it."""
    pid = os.getpid()
    _, started = _state(pid)
    return {"pid": pid, **({"started": started} if started is not None else {})}


def running(owner: Any) -> bool:
    """Whether the process a record names is still the one running under that pid. A record with
    no owner (written before owners were) is a leftover, as every claim used to be read."""
    if not isinstance(owner, dict):
        return False
    try:
        pid = int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    alive, started = _state(pid)
    if not alive:
        return False
    recorded = owner.get("started")
    if recorded is None or started is None:
        return True
    return int(recorded) == started
