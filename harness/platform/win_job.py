"""A Windows Job Object with KILL_ON_JOB_CLOSE, so a process tree dies with Reeve.

`taskkill /T` only runs on an orderly teardown and cannot reach a descendant that
re-parented (or sits in another job); if Reeve itself dies, anything it spawned
survives forever — PTY shells, build trees, dev servers. A job object makes the death
structural: the child is assigned to a job whose only handle this process holds, and
Windows terminates every process in the job when that handle closes, for any reason.

Best-effort by contract, as for other process helpers: on POSIX — or
whenever creating or assigning the job fails, including inside a parent job that does
not allow breakaway — this returns None and the existing kill paths (taskkill /T,
process groups) stay in charge. A containment helper that can fail a spawn would be a
new way for a turn to die, which is exactly what the harness refuses.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import Any, Optional

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

#: JobObjectExtendedLimitInformation
_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _LargeInteger(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LargeInteger),
        ("PerJobUserTimeLimit", _LargeInteger),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProcessJob:
    """One assigned process tree. `terminate()` kills it now; `close()` releases the
    handle, and Windows kills whatever is left in the job when the last handle closes."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    @classmethod
    def attach(cls, pid: int) -> Optional["ProcessJob"]:
        """Put `pid` (and everything it spawns later) in its own kill-on-close job.

        Returns None on POSIX or on any failure; callers must keep their existing kill.
        """
        if not _IS_WINDOWS or not pid:
            return None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return None
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                handle,
                _EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
            if not ok:
                kernel32.CloseHandle(handle)
                return None
            access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
            process = kernel32.OpenProcess(access, False, int(pid))
            if not process:
                kernel32.CloseHandle(handle)
                return None
            try:
                assigned = kernel32.AssignProcessToJobObject(handle, process)
            finally:
                kernel32.CloseHandle(process)
            if not assigned:
                # A parent job without breakaway refuses the assignment; the process is
                # already ours either way, so there is nothing to undo but the handle.
                kernel32.CloseHandle(handle)
                return None
            return cls(handle)
        except Exception:  # noqa: BLE001 - containment is best-effort, never fatal
            logger.debug("could not attach a job object to pid %s", pid, exc_info=True)
            return None

    def terminate(self) -> None:
        if not self._handle:
            return
        try:
            ctypes.WinDLL("kernel32", use_last_error=True).TerminateJobObject(
                self._handle, 1
            )
        except Exception:  # noqa: BLE001 - the existing kill path still runs
            logger.debug("TerminateJobObject failed", exc_info=True)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if not handle:
            return
        try:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:  # pragma: no cover - GC timing is not a contract
        self.close()


__all__ = ["ProcessJob"]
