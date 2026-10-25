"""The independent verdict: what the `verify` subagent answered, and whether it still holds.

The checker half of a maker/checker pair answers `VERDICT: PASS | FAIL | UNVERIFIED`. This module
reads the newest one from the transcript alone, and how many work edits came after the checker was ASKED.
Split from `verification.py`, which keeps the shell-check ledger: the two read the same transcript
for different facts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .call_pairs import answered_calls
from .work_edits import is_work_edit, work_edits_after

TASK_TOOL = "task"
VERIFY_AGENT = "verify"
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL|UNVERIFIED)\b")


@dataclass(frozen=True)
class Verdict:
    """The newest independent verdict: what the `verify` subagent answered, and how
    many file edits came after it was ASKED (an edit made once the checker had started is
    work it may never have seen, whenever the verdict itself arrived)."""

    verdict: str
    edits_since: int


_TASK_ID_RE = re.compile(r'<task id="([^"]+)"')


def _task_agent(call: Optional[Mapping[str, Any]]) -> str:
    """The `subagent_type` a `task` call asked for, or "" for any other call."""
    function = (call or {}).get("function") or {}
    if str(function.get("name") or "") != TASK_TOOL:
        return ""
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            arguments = {}
    return str((arguments or {}).get("subagent_type") or "") if isinstance(arguments, Mapping) else ""


def last_verdict(
    messages: Sequence[Mapping[str, Any]], exempt: Sequence[str] = ()
) -> Optional[Verdict]:
    """The newest verdict the `verify` subagent returned, wherever it arrived.

    A verify task answers either as its `task` tool result (foreground) or, when it ran in
    the background, as the completion envelope delivered to the session later — both
    carry the same `<task id="…">`. Reading only the tool result made every background
    verdict invisible: session b3a8491e-9db (2026-09-28) got PASS from a background verify
    in goal round 4, the done-gate could not see it, and the model re-asked verify for six
    more rounds. A verdict counts only from a task this session started as `verify`.

    `edits_since` is dated from the ASK, not from the answer. The checker reads
    the tree while it runs, so an edit the maker makes meanwhile is one the verdict may not
    cover — and when the verdict arrived as a later message, counting from that message
    counted none of them: a PASS for a tree the checker never saw read as "0 edits since"
    and opened the done-gate. Session b3a8491e-9db edited files during three of its
    background verify runs. Dated from the ask, such a PASS reads "verify again"."""
    ordered = list(messages)
    #: verify task id -> where its newest ask sits: (assistant message index, call position).
    asked: dict[str, tuple[int, int]] = {}
    call_sites: dict[int, tuple[int, int]] = {}
    found: Optional[str] = None
    found_ask: Optional[tuple[int, int]] = None
    for index, message, call in answered_calls(ordered):
        content = str(message.get("content") or "")
        role = message.get("role")
        if role == "assistant":
            for position, own in enumerate(message.get("tool_calls") or []):
                call_sites[id(own)] = (index, position)
            continue
        if role == "tool":
            if _task_agent(call) != VERIFY_AGENT:
                continue
            site = call_sites.get(id(call), (index, 0))
            task_ids = _TASK_ID_RE.findall(content)
            for task_id in task_ids:
                asked[task_id] = site
            ask = site
        elif role == "user":
            sites = [asked[t] for t in _TASK_ID_RE.findall(content) if t in asked]
            if not sites:
                continue
            ask = max(sites)
        else:
            continue
        match = _VERDICT_RE.search(content)
        if match:
            found, found_ask = match.group(1), ask
    if found is None or found_ask is None:
        return None
    return Verdict(verdict=found, edits_since=_work_edits_since_ask(ordered, found_ask, exempt))


def _work_edits_since_ask(
    ordered: Sequence[Mapping[str, Any]], ask: tuple[int, int], exempt: Sequence[str]
) -> int:
    """Work edits issued after the call at `ask` (message index, position in its message):
    the calls that follow it in the same assistant message, and every later message's."""
    index, position = ask
    edits = 0
    for call in (ordered[index].get("tool_calls") or [])[position + 1 :]:
        function = call.get("function") or {}
        if is_work_edit(str(function.get("name") or ""), function.get("arguments"), "", exempt):
            edits += 1
    return edits + work_edits_after(ordered, index, exempt=exempt)


__all__ = ["TASK_TOOL", "VERIFY_AGENT", "Verdict", "last_verdict"]
