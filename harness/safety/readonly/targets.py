"""The files a read-only command would read, so they can be held to the session's roots.

`classify` decides what a command may do and says nothing of what it may read, so a grant meant as
"stop asking about my project files" would also have covered a cloud credentials file, an SSH key
or Reeve's own secrets file. Naming the file operands lets the caller check each against the roots,
as browser uploads are checked.

Pulling read targets out of arbitrary shell cannot be done in general. It works here only because
the classifier has already narrowed the input to the verbs it allows.
"""

from __future__ import annotations

import posixpath
import re
from typing import Optional

from .lexing import command_stages, without_assignments

# These take no file operands: their arguments are text, character sets or command names.
_TAKES_NO_PATHS = {
    "echo", "printf", "pwd", "whoami", "id", "date", "uname", "true", "false",
    "which", "basename", "dirname", "tr", "command", "env",
}
# The first operand is a pattern, script or filter. Any after it are files.
_PATTERN_THEN_FILES = {"grep", "egrep", "fgrep", "rg", "ugrep", "jq", "awk", "gawk", "mawk", "nawk", "sed"}
# Flags whose value is a path.
_PATH_FLAGS = {"-f", "--file", "--exclude-from", "--include-from"}
# `head -n 5`, `cut -f 1` and `sed -n 2p` carry a count or a field number. Such a token is never a
# file worth scoping, and dropping it spares a table of which flag takes a number.
_COUNT = re.compile(r"^[0-9]+([,:.-][0-9]+)*[a-zA-Z]?$")


def _git_targets(args: list[str]) -> list[str]:
    # Only `-C <dir>` leaves the working directory; the rest of what the classifier accepts reads
    # the repository already in scope. Operands after `--` are pathspecs.
    found: list[str] = []
    for at, token in enumerate(args):
        if token == "-C" and at + 1 < len(args):
            found.append(args[at + 1])
        elif token == "--":
            found.extend(t for t in args[at + 1 :] if not t.startswith("-"))
            break
    return found


def _stage_targets(argv: list[str]) -> list[str]:
    argv = without_assignments(argv)
    if not argv:
        return []
    head, args = argv[0], argv[1:]
    if head in _TAKES_NO_PATHS:
        return []
    if head == "git":
        return _git_targets(args)

    found: list[str] = []
    value_is_path = False
    seen_operand = False
    for token in args:
        if value_is_path:
            # `-f` names a pattern file for grep and a field number for cut; the count test tells
            # them apart without a per-command table.
            if not _COUNT.match(token):
                found.append(token)
            # With `grep -f patterns.txt build.log` the flag supplied the pattern, so the first
            # positional is already a file and must not be skipped as the pattern.
            seen_operand = True
            value_is_path = False
            continue
        if token.startswith("-"):
            # `diff --from-file=/etc/x a`: a value that looks like a path is scoped like an operand.
            value = token.partition("=")[2] if token.startswith("--") else ""
            if value and ("/" in value or "\\" in value or value.startswith("~")):
                found.append(value)
            if token in _PATH_FLAGS:
                value_is_path = True
            elif head == "find":
                break  # find's predicates begin here, and its paths come before them
            continue
        if head in _PATTERN_THEN_FILES and not seen_operand:
            seen_operand = True
            continue
        seen_operand = True
        if not _COUNT.match(token):
            found.append(token)
    return found


def read_targets(command: str) -> Optional[list[str]]:
    """Every file operand `command` would read, or None when they cannot be named. It assumes
    `is_readonly_command` accepted it.

    It errs toward naming more: an extra operand costs one manual approval and a missed one is an
    unscoped read. A path reached through a flag outside `_PATH_FLAGS` is not returned; the
    positional operands, where the real exposure is, are covered.

    A backslash is read as a path separator. The POSIX lexer takes it for an escape, which turned
    PowerShell's `cat C:\\Users\\me\\.aws\\credentials` into the relative `C:Usersme.awscredentials`,
    a name inside the root; bash does drop the backslashes, and the separator reading is the
    stricter of the two answers."""
    stages = command_stages(str(command or "").replace("\\", "/"))
    if stages is None:
        return None
    # A `cd` is itself a read of where it goes, and the commands after it read from there.
    found: list[str] = []
    here = ""
    for stage in stages:
        argv = without_assignments(stage)
        if argv and argv[0] in ("cd", "Set-Location") and len(argv) == 2:
            here = _from(here, argv[1])
            found.append(here)
            continue
        found.extend(_from(here, target) for target in _stage_targets(stage))
    return found


def _from(here: str, target: str) -> str:
    """`target` as read from `here` ("" for the shell's own folder)."""
    if not here or target.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", target):
        return target
    return posixpath.join(here, target)
