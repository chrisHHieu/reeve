"""`cmd.exe` as a `run_shell` dialect.

cmd has no way to ask for the previous command's status from a LATER line the way bash and
PowerShell do (`cmd /c` takes one line), so the trailer rides on the same line and is measured
(2026-10-01, Windows 11, cmd.exe 10.0):

  - `%ERRORLEVEL%` and `%CD%` are expanded when the line is PARSED, before the command has run, so
    a plain `& echo %ERRORLEVEL%` reports the previous value. `call echo %^ERRORLEVEL% %^CD%` defers
    the expansion to when `call` runs it: exit 7 reads 7, `cd "sub dir"` reads the new directory,
    an unknown program reads 9009.
  - The command is wrapped in parentheses: appended bare, the trailer becomes the body of a `for` or
    `if` the command ends with, and printed once per iteration (measured: two markers for a
    two-item `for`).
  - A bare `&` with no space before it: `echo hi &` prints `hi ` with the space.
  - `echo.` first, so a program that ended without a newline does not swallow the marker line.
  - `chcp 65001` first: cmd's own output is the OEM code page otherwise, and the reader decodes UTF-8.
  - The line goes to CreateProcess exactly as built (`spawn` takes a string for this): a list would
    be quoted with `\\"`, which cmd does not understand.

A command of several lines is a batch script, not a line: it is written to a temporary `.cmd` file
and `call`ed, which also gives it batch semantics (`%%i`, `exit /b N`) — what someone writing several
lines of cmd means. A one-line command keeps interactive semantics (`for %i in (…) do …`).
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

#: The code page change that makes cmd's own output UTF-8; silenced, it prints a banner otherwise.
_CHCP = "chcp 65001>nul"


def cmd_trailer(marker: str) -> str:
    """The suffix that reports `<marker> <exit> <cwd>` (parsed like every other dialect's)."""
    return f"& echo.& call echo {marker} %^ERRORLEVEL% %^CD%"


def compose(command: str, marker: Optional[str]) -> tuple[str, list[str]]:
    """`(script, temporary files)` for one cmd command.

    `script` is what follows `/c`; the temporary files are the caller's to delete once the process
    has ended. With no `marker` (a background task) there is no trailer."""
    body = command.strip("\r\n")
    temps: list[str] = []
    if "\n" in body.replace("\r\n", "\n").strip():
        handle, path = tempfile.mkstemp(prefix="reeve-", suffix=".cmd")
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write("@echo off\r\n" + body.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n")
        temps.append(path)
        body = f'call "{path}"'
    script = f"({body})"
    if marker:
        script += cmd_trailer(marker)
    return script, temps


def command_line(shell_path: str, script: str) -> str:
    """The whole command line for CreateProcess: `/d` skips AutoRun, `/s /c "…"` strips exactly the
    outer quotes and leaves the script's own alone.

    Two cmd processes, the outer only to run `chcp 65001`: a cmd decides how it writes its OWN output
    (`echo`, `dir`) when it STARTS, so `chcp` inside the one that runs the script changes the
    console and not that process (measured: `chcp` reports 65001 and the next `echo` still writes
    `Nguy?n`), while a cmd started AFTER it writes UTF-8. A Vietnamese file name in `dir` depends
    on it."""
    return f'"{shell_path}" /d /s /c "{_CHCP} & "{shell_path}" /d /s /c "{script}""'
