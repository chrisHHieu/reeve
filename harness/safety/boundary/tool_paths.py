"""The paths and commands named in a tool's arguments."""

from __future__ import annotations

import re
from typing import Any

# The argument carrying the command an exec tool would run. Two places inspect a command, the
# self-protection floor and the read-only session grant, and a tool that spelled its argument
# differently would slip past both while still classed as exec. One map means adding an exec tool
# is one edit and not a hunt.
_COMMAND_ARG: dict[str, str] = {"run_shell": "command", "terminal_send": "text"}

# The argument naming a write tool's target when it is one top-level field. Patch and diff tools
# carry their paths inside the text instead, which `write_paths` extracts.
_PATH_ARG: dict[str, str] = {"write_file": "path", "replace_in_file": "path", "edit_notebook": "path"}
# `apply_patch` file headers, and the `+++ b/<path>` header of a unified diff.
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)
_DIFF_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)

# The argument naming what a read tool looks at. `tools/batch.py` uses it to decide whether two
# calls in one batch can overlap, which is not root scoping: it matters only when a read might race
# a write, and it needs the path they would name.
#
# A read tool missing from this table is assumed to touch nothing a write could race.
# `search_history`, `todo_write`, `memory_read` and the rest of the low-risk set read the session or
# the memory store and not the workspace, which is what their metadata already assumed.
_READ_ARG: dict[str, str] = {
    "read_file": "path",
    "read_image": "path",
    "list_files": "path",
    "search_files": "path",
    "grep": "path",
}


def command_text(tool_name: str, arguments: dict[str, Any]) -> str:
    """The command an exec tool would run, or "" when it carries none. `terminal_open` is exec
    because it starts a shell but it runs nothing itself, so "" is its true answer and not a
    missing one."""
    argument = _COMMAND_ARG.get(tool_name)
    return str((arguments or {}).get(argument, "")) if argument else ""


def write_paths(tool_name: str, arguments: dict[str, Any]) -> tuple[list[str], bool]:
    """Every filesystem path a write tool would touch, for root scoping, as `(paths, located)`.

    `located` is False when the path cannot be determined: an unknown write tool, or a patch or
    diff with no file header that parses. The caller then has to fail closed and not skip scoping,
    so an unscoped write cannot slip through an automatic mode."""
    argument = _PATH_ARG.get(tool_name)
    if argument is not None:
        value = arguments.get(argument)
        return ([str(value)], True) if value else ([], False)
    if tool_name == "apply_patch":
        text = str(arguments.get("patch", ""))
        paths = _PATCH_FILE.findall(text) + _PATCH_MOVE.findall(text)
        return ([path.strip() for path in paths], bool(paths))
    if tool_name == "apply_unified_diff":
        text = str(arguments.get("diff", ""))
        paths = [path for path in _DIFF_FILE.findall(text) if path and path != "/dev/null"]
        return (paths, bool(paths))
    # An unknown write tool, such as one a user override promoted to write, has a path we cannot
    # find, so it cannot be scoped automatically.
    return ([], False)


def reads_files(tool_name: str) -> bool:
    """A tool that reads the filesystem at a path it names (`read_file`, `grep`, …)."""
    return tool_name in _READ_ARG


def read_paths(tool_name: str, arguments: dict[str, Any]) -> tuple[list[str], bool]:
    """Every path a read tool would look at, for finding conflicts within a batch, as
    `(paths, located)`.

    `located` is False when a read tool was given no path at all. The call is malformed and not
    unscoped, but the careful reading is the one `write_paths` takes: a reach we do not know must
    not be assumed to miss the others."""
    argument = _READ_ARG.get(tool_name)
    if argument is None:
        return ([], True)
    value = (arguments or {}).get(argument)
    if value is None or str(value) == "":
        return ([], False)
    return ([str(value)], True)
