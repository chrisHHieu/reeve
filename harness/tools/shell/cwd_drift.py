"""Saying where a failed command ran, when an earlier `cd` moved the shell away from the session folder.

A `cd` lasts across commands, as in Claude Code, and the status line names the folder after every
command. A model that missed it ran `cd surfaces/gui && npm test` while already in `surfaces/gui`,
and `.venv/Scripts/python` from there: six failed commands in one session (2026-10-15), each read
as a broken tool. Claude Code only moves the shell back when it leaves the project, which none of
those did. The harness saw which command moved the shell, so a failure that reads like a wrong
path says so, with that command. A command that worked gets no note.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# What a shell, a runtime or a package manager prints for a path or a name it could not find.
_NOT_FOUND = re.compile(
    r"No such file or directory|command not found|can't open file|cannot access"
    r"|Cannot find path|is not recognized as|ObjectNotFound|does not exist"
    r"|cannot find the path specified|cannot find the file specified"
    r"|Cannot find module|ENOENT|No module named|Missing script",
    re.IGNORECASE,
)


# A command that changes folder itself (`cd x && …`, `Set-Location x; …`) chose where it runs. It is
# only told when that change is what failed: `cd gui` from inside `gui` is the drift case.
_OWN_CD = re.compile(r"(?:^|[;&|\n(]\s*)(?:cd|chdir|pushd|Set-Location|sl)(?:\s|$)", re.IGNORECASE)
_CD_FAILED = re.compile(r"\bcd: |\bpushd: |Set-Location|cannot find the path specified", re.IGNORECASE)


def counts_as_drift(command: str, text: str) -> bool:
    """Whether a failure that reads like a wrong path is about where the shell already was."""
    return bool(_NOT_FOUND.search(text)) and (not _OWN_CD.search(command or "") or bool(_CD_FAILED.search(text)))


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def drift_note(
    result: dict[str, Any], *, cwd: str, root: str, moved_by: Optional[str], command: str = ""
) -> Optional[str]:
    """The sentence a failed command gets when it ran away from the session folder, or None."""
    if _same(cwd, root):
        return None
    code = result.get("exit_code")
    if code == 0 or result.get("status") == "background":
        return None
    text = f"{result.get('output') or ''}\n{result.get('error') or ''}"
    if not counts_as_drift(command, text):
        return None
    moved = f" `{moved_by.strip()[:120]}` moved it there and" if moved_by else ""
    return (
        f"this ran in {cwd}, not the session folder {root}:{moved} a cd lasts across commands. "
        "A path or a command written from the session folder is looked up from here."
    )
