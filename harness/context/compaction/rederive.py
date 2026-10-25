"""The faithful tier of a cut: aged output of a call that can be made again leaves the view.

A summary rewrites what it keeps and can say what the session never saw (Slipstream, arXiv
2605.08580: a scoped edit summarized into a global one); a cut that only removes cannot. This tier
removes whole outputs and leaves a line naming the call that made each one, or the file it was
saved to (`prune_tool_results(rederive=True)` in `tokens.py`).
"""

from __future__ import annotations

import json
from typing import Any

#: The faithful tier (`prune_tool_results(rederive=True)`): aged output of a call that can be made
#: again leaves the view whole, replaced by a line that names the call. Long sessions are half this:
#: across 11 sessions over 60k tokens (2026-09-25..10-10), tool results were 61% of the history and
#: output of re-runnable calls older than the newest twenty results 49%. Results under this size stay:
#: the line that replaces one costs about as much (CliffCompaction, arXiv 2609.26779, keeps < 500).
REDERIVE_MIN_CHARS = 500

#: The working trim (`TurnEngine._working_trim`) runs only when it removes at least this many tokens
#: and this share of the request: a batch large enough to repay the cache it costs.
TRIM_MIN_TOKENS = 20_000
TRIM_MIN_SHARE = 0.25

#: A stage compaction (`TurnEngine._stage_due`) runs once the context is past this share of the
#: compaction trigger and a stage of the work just ended verified: about 190k of the 380k a 400k
#: window triggers at, where CliffCompaction (arXiv 2609.26779) sets its default of 200k.
STAGE_SHARE = 0.5
#: ...and only when what it summarizes, everything since the last cut, is at least this share of
#: the trigger: a stage cut that takes a few thousand tokens pays a summary and a cold cache for
#: nothing, and the threshold cut follows soon after.
STAGE_MIN_SHARE = 0.25

#: Read-only calls whose output is made again by calling them again, and the argument that says
#: what they read. The output now is what the work needs anyway: a file read thirty steps ago may
#: have been edited since.
REREAD_TOOLS = {
    "read_file": "path",
    "list_files": "path",
    "grep": "pattern",
    "lsp": "path",
}

#: Calls whose output cannot simply be asked for again (a command may not be repeatable, a page
#: changes): their aged output is saved to a file and the line names it.
SAVED_TOOLS = frozenset({"run_shell", "shell_task_output", "web_fetch", "web_search"})


def _brief_arguments(arguments: Any) -> str:
    """A call's arguments as one short line, for the line that stands in for its output."""
    if not isinstance(arguments, dict):
        return ""
    parts = []
    for key, value in arguments.items():
        if value in (None, "", [], {}):
            continue
        text = " ".join(str(value).split())
        parts.append(f"{key}={text[:80] + '…' if len(text) > 80 else text}")
    line = ", ".join(parts)
    return line[:159] + "…" if len(line) > 160 else line


def rederive_stub(name: str, arguments: Any, content: str) -> str:
    """What replaces an aged re-runnable output: the call that made it, and that it is gone from
    the view. Announces itself, like every stub here."""
    lines = content.count("\n") + 1
    return (
        f"[{name} output dropped from context to keep it small — {lines:,} lines from "
        f"{name}({_brief_arguments(arguments)}); call it again to see it as it is now]"
    )


def call_names(messages: list[dict[str, Any]], upto: int) -> dict[int, tuple[str, Any]]:
    """For each tool result before `upto`, the `(name, arguments)` of the call it answers: the newest
    call before it with its id, since an id repeats across a session (Gemini's `call_0`)."""
    seen: dict[str, tuple[str, Any]] = {}
    out: dict[int, tuple[str, Any]] = {}
    for index in range(min(upto, len(messages))):
        message = messages[index]
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (TypeError, ValueError):
                    arguments = {}
                seen[str(call.get("id") or "")] = (str(function.get("name") or ""), arguments)
        elif role == "tool":
            found = seen.get(str(message.get("tool_call_id") or ""))
            if found is not None:
                out[index] = found
    return out


def rederivable(name: str, content: Any) -> bool:
    """Whether the faithful tier takes this result out of the view (the saved kind needs a sink,
    which the view builder has; a result too small to be worth its line stays)."""
    return isinstance(content, str) and len(content) > REDERIVE_MIN_CHARS and (
        name in REREAD_TOOLS or name in SAVED_TOOLS
    )


__all__ = ["STAGE_SHARE", "STAGE_MIN_SHARE", "TRIM_MIN_TOKENS", "TRIM_MIN_SHARE", "REDERIVE_MIN_CHARS", "REREAD_TOOLS", "SAVED_TOOLS", "rederivable", "rederive_stub", "call_names"]
