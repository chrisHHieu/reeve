"""Reading a background job's whole output: the lines a pattern matches, or a window from a line.

`shell_task_output` returns what is NEW since the last read. A job's log grows (a dev server, a
watcher, a long test run), and finding the one line that said what went wrong meant reading it all
again, or running `findstr`/`grep` over a file the job never wrote. These two reads look at
everything the job has printed and leave the incremental cursor where it was, so the next plain
read still picks up where the last one stopped.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Matching lines one search shows; past this it says how many more there were.
MAX_MATCHES = 200
#: Lines one window from `from_line` shows at most (the character budget may stop it sooner).
MAX_WINDOW_LINES = 400
#: A line is clipped here, as `grep` clips its matches.
LINE_CHARS = 300


def search_lines(lines: list[str], pattern: str, *, ignore_case: bool = False) -> dict[str, Any]:
    """The lines of `lines` that `pattern` (a Python regular expression) matches, each with its
    1-based number in the whole output."""
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return {"error": f"invalid pattern: {exc}"}
    hits = [(number, line.rstrip("\r\n")) for number, line in enumerate(lines, 1) if rx.search(line)]
    shown = hits[:MAX_MATCHES]
    body = "\n".join(f"{number}: {text[:LINE_CHARS]}" for number, text in shown)
    result: dict[str, Any] = {
        "output": body,
        "pattern": pattern,
        "matches": len(hits),
        "shown": len(shown),
        "total_lines": len(lines),
    }
    if len(hits) > len(shown):
        result["truncated"] = True
        result["note"] = (
            f"{len(hits) - len(shown)} more matching lines: narrow the pattern, or read around "
            f"one with from_line"
        )
    return result


def lines_from(lines: list[str], start: int, budget: int) -> dict[str, Any]:
    """The output from line `start` (1-based), at most `MAX_WINDOW_LINES` lines and `budget`
    characters, with the line the next window starts at when there is more."""
    total = len(lines)
    first = max(1, int(start))
    if first > total:
        return {"output": "", "from_line": first, "to_line": first - 1, "total_lines": total,
                "note": f"line {first} is past the end: the job has printed {total} lines so far"}
    taken: list[str] = []
    used = 0
    for line in lines[first - 1 : first - 1 + MAX_WINDOW_LINES]:
        text = line.rstrip("\r\n")
        if taken and used + len(text) + 1 > budget:
            break
        taken.append(text)
        used += len(text) + 1
    last = first + len(taken) - 1
    result: dict[str, Any] = {
        "output": "\n".join(taken),
        "from_line": first,
        "to_line": last,
        "total_lines": total,
    }
    if last < total:
        result["next_from_line"] = last + 1
    return result


def whole_output(
    lines: list[str], pattern: Optional[str], from_line: Optional[int], ignore_case: bool, budget: int
) -> dict[str, Any]:
    """The search when there is a pattern, else the window from `from_line`."""
    if pattern:
        return search_lines(lines, pattern, ignore_case=ignore_case)
    return lines_from(lines, int(from_line or 1), budget)
