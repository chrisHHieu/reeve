"""What the harness itself saw of the work, written into a cut of the context beside the summary.

A summary is a model's prediction of what the rest of the session will need, and most of what goes
wrong after a compaction is an omission (Slipstream, arXiv 2605.08580: about 90% of failures, most
seen within three steps); an agent then also fails to follow its own summary (AutoCompact, arXiv
2610.02163: a summary that logged an unresolved error and proposed finishing). The harness knows some
of it without asking anyone: which check ran last and what it said, and what was edited since. That
goes in as a record the model did not write, and where the summary says otherwise, the record is what
happened (what the harness witnessed, the harness keeps).
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from harness.safety.boundary.tool_paths import write_paths
from harness.tools.checks import shown_command
from harness.tools.checks.marks import failure_marks, is_setup, spoken

from .verification import last_verification

#: Failing names listed before the rest are counted.
MAX_NAMES = 8
#: Edited files listed before the rest are counted.
MAX_FILES = 12

HEADING = (
    "## Harness record (as of this cut; seen by the harness, not written by the model; where the "
    "summary above says otherwise, this is what happened)"
)


def _arguments(call: Mapping[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def edited_after(messages: Sequence[Mapping[str, Any]], start: int) -> list[str]:
    """The files written after transcript index `start`, newest first, each once."""
    seen: list[str] = []
    for message in reversed(messages[max(0, start) + 1 :]):
        if message.get("role") != "assistant":
            continue
        for call in reversed(message.get("tool_calls") or []):
            name = str((call.get("function") or {}).get("name") or "")
            paths, located = write_paths(name, _arguments(call))
            for path in paths if located else ():
                if path not in seen:
                    seen.append(path)
    return seen


def _listed(items: list[str], limit: int) -> str:
    shown = ", ".join(f"`{item}`" for item in items[:limit])
    more = len(items) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def witnessed_record(messages: Sequence[Mapping[str, Any]]) -> str:
    """The record for a cut made now, or "" when there is nothing to say (no check run, nothing
    edited)."""
    check = last_verification(messages)
    lines: list[str] = []
    if check is None:
        edited = edited_after(messages, -1)
        if not edited:
            return ""
        lines.append(f"- No check has run in this session. Edited: {_listed(edited, MAX_FILES)}.")
    else:
        if check.ok is False:
            result = messages[check.index].get("content") if 0 <= check.index < len(messages) else ""
            marks = sorted(failure_marks(str(result or "")))
            names = [spoken(mark) for mark in marks if not is_setup(mark)]
            setup = [spoken(mark) for mark in marks if is_setup(mark)]
            verdict = "FAILED" + (f" ({check.summary})" if check.summary else "")
            if names:
                verdict += f"; failing: {_listed(names, MAX_NAMES)}"
            elif setup:
                verdict += f"; it could not run: {_listed(setup, MAX_NAMES)}"
        elif check.ok is True:
            verdict = "passed" + (f" ({check.summary})" if check.summary else "")
        else:
            verdict = "ended without a verdict" + (" (timed out)" if check.timed_out else "")
        lines.append(f"- Last check: `{shown_command(check.command)}` {verdict}.")
        edited = edited_after(messages, check.index)
        lines.append(
            f"- Edited since that check: {_listed(edited, MAX_FILES)}; it has not run on them."
            if edited
            else "- Nothing has been edited since that check."
        )
    return "\n".join([HEADING, *lines])


__all__ = ["HEADING", "edited_after", "witnessed_record"]
