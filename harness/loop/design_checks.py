"""DesignCheckMixin: a design file just written is opened and measured before the model reads the
result.

A design page is checked after every write that touches it, by the harness's own browser
(`tools/design`), and what was measured joins the write's result the way a syntax error or a
language server's errors do: an aside on the write, never a rewrite of it. The
model does not have to remember to check, and cannot shape what the check says.

The check runs here, on the turn's own loop, after the write and before the result is recorded,
because it is a browser and takes about a second: `_record_result` is synchronous and holds no
await. A stop ends the wait, as it ends the wait for any call. A check that fails says so in one
line, and a write is never failed by its check.

What is checked:
- only a write that succeeded, by a write tool, of a design page (`tools/design/pages.py`): a page
  under a `design/` folder. Other HTML (a flow drawn as a page, a report) is a file, and is not
  checked: every page that opened by itself was checked for a day, and a diagram nobody asked to
  have designed got a design's check, card and tab (owner, 2026-10-20);
- at most `MAX_FILES` files of one call, each at the default widths;
- the screenshots go to the model only when something is out of bounds: a page with nothing to fix
  costs one line of text, not three images.

Nothing is checked when the session has no checker (the `design` feature removed, a remote session,
no browser on the machine).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

from harness.llm.providers import ToolCall
from harness.safety.boundary import write_paths
from harness.session.turns import ATTACHED_BY
from harness.tools.design import is_design_file

from .code_changes import CODE_WRITE_TOOLS
from .nudges import DESIGN_UNFIXED_NUDGE_TEXT

#: How many files of one call are checked. A patch can touch many; the first few are the page.
MAX_FILES = 2
#: The longest a turn waits on one check before carrying on without it.
CHECK_DEADLINE_S = 45.0
#: How a part the person pointed at, or pinned a comment on, is named in their message (the
#: preview's attachments, `surfaces/gui/src/components/rail/designAsks.ts`).
_POINTED = re.compile(r"^\s*selector: (.+)$", re.MULTILINE)
#: The first line of design_guide's text: proof the guide reached the model however it was called.
_GUIDE_MARK = "How a design is made in Reeve"
#: A skill about how interfaces look, read from its name and the start of its instructions: a design
#: skill the person installed is the taste they chose, as DESIGN.md is (2026-10-21).
_DESIGN_SKILL = re.compile(r"design|front-?end|\bui\b|\bux\b|interface|visual|typography|landing", re.IGNORECASE)
_SKILL_HEAD_CHARS = 400


def _design_skill_loaded(content: Any) -> bool:
    """Whether a `load_skill` result loaded a design skill (an error loaded nothing)."""
    text = str(content or "")
    if '"error"' in text[:40]:
        return False
    name = re.search(r'"name":\s*"([^"]+)"', text)
    head = text.split('"instructions":', 1)[-1][:_SKILL_HEAD_CHARS] if '"instructions":' in text else ""
    return bool(name and (_DESIGN_SKILL.search(name.group(1)) or _DESIGN_SKILL.search(head)))


def guide_read(messages: list) -> bool:
    """Whether design_guide was called in this conversation, by name or through the deferred-tool
    bridge, or its text is in a tool result, or a design skill was loaded. A real session loaded the
    person's own `frontend-design` skill, drew by it, and was told on each of 25 writes to read the
    guide and redraw (2026-10-19)."""
    skill_calls: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = (call or {}).get("function") or {}
                name = function.get("name")
                if name == "design_guide" or (name == "tool_call" and "design_guide" in str(function.get("arguments") or "")):
                    return True
                if name == "load_skill" and (call or {}).get("id"):
                    skill_calls.add(str(call["id"]))
        elif message.get("role") == "tool":
            content = message.get("content")
            if _GUIDE_MARK in str(content or ""):
                return True
            if str(message.get("tool_call_id") or "") in skill_calls and _design_skill_loaded(content):
                return True
    return False


def pointed_at(messages: list) -> tuple[str, ...]:
    """The parts named in the person's latest message: what a write may change without being
    told it changed something it was not asked to."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user" or message.get(ATTACHED_BY):
            continue
        content = message.get("content")
        parts = content if isinstance(content, list) else [content]
        text = "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part or "") for part in parts)
        return tuple(dict.fromkeys(match.strip() for match in _POINTED.findall(text)))[:20]
    return ()


class DesignCheckMixin:
    async def _check_written_designs(self, tool_call: ToolCall, status: str) -> None:
        """Check the design files this successful write touched, and keep the reports for
        `_record_result` under the call's id."""
        checker = getattr(self, "design_checker", None)
        if checker is None or status != "ok" or tool_call.name not in CODE_WRITE_TOOLS:
            return
        paths, located = write_paths(tool_call.name, dict(tool_call.arguments or {}))
        if not located:
            return
        root = Path(str(self.permissions.workspace_root))
        await self._check_drift(tool_call, [root / raw if not Path(raw).is_absolute() else Path(raw) for raw in paths if raw])
        targets: list[tuple[str, Path]] = []
        for raw in paths:
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.is_file() and is_design_file(candidate):
                targets.append((str(raw).replace("\\", "/"), candidate))
            if len(targets) == MAX_FILES:
                break
        if not targets:
            return
        reports: list[Any] = []
        asked = pointed_at(self.messages)
        for shown, candidate in targets:
            work = asyncio.ensure_future(asyncio.to_thread(checker, candidate, shown, asked))
            stop = asyncio.ensure_future(self._cancel.wait())
            try:
                done, _ = await asyncio.wait(
                    {work, stop}, timeout=CHECK_DEADLINE_S, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                stop.cancel()
            if work not in done:
                # Detached, not cancelled: the browser runs to its own deadline in its thread.
                work.add_done_callback(lambda future: future.exception())
                break
            try:
                reports.append(work.result())
            except Exception:  # a check that costs the call is worse than none
                continue
        if reports:
            # A page drawn without the guide is drawn without its taste: the write's result says so
            # until the guide is read. A real session never fetched the cold tool (2026-10-19).
            if not guide_read(self.messages):
                for report in reports:
                    if hasattr(report, "unguided"):
                        report.unguided = True
            self.__dict__.setdefault("_design_reports", {})[tool_call.id] = reports

    async def _check_drift(self, tool_call: ToolCall, written: list[Path]) -> None:
        """A theme token a reviewed design uses that this write moved (step 5): file reads
        only, and nothing at all unless a design was reviewed and a theme-like file was written."""
        drift = getattr(self, "design_drift", None)
        if drift is None or not written:
            return
        try:
            found = await asyncio.wait_for(asyncio.to_thread(drift, written), CHECK_DEADLINE_S)
        except Exception:
            return
        if found:
            self.__dict__.setdefault("_design_drift", {})[tool_call.id] = list(found)

    def _note_design_standing(self, reports: list[Any]) -> None:
        """The last check of each page written this turn, by what it still has to fix."""
        standing = self.__dict__.setdefault("_turn_design_open", {})
        for report in reports:
            if not getattr(report, "error", ""):
                standing[report.shown] = int(report.issues)

    def _nudge_unfixed_design(self) -> bool:
        """One note when the turn is about to end with a design page whose last check this turn
        still found something to fix. True when the caller should go around again.

        The check's findings rode the write's result and a real session answered over three of them
        (2026-10-19): nothing between the last check and the answer asked. Once a turn, like the
        red-check note: a gate that can loop is worse than one that can be ignored once."""
        if self.__dict__.get("_turn_design_nudged") or self._cancel.is_set() or self._turn_truncated:
            return False
        if "design_nudge" in self.ablated:
            return False
        open_pages = [(page, n) for page, n in self.__dict__.get("_turn_design_open", {}).items() if n]
        if not open_pages:
            return False
        self._turn_design_nudged = True
        listed = ", ".join(f"{page} ({n} to fix)" for page, n in open_pages[:3])
        self._attach_note(DESIGN_UNFIXED_NUDGE_TEXT.format(pages=listed), "design_nudge")
        self.log.append("verify/nudge", turn=self._turn_no, step=self._step_no, reason="design_unfixed", count=len(open_pages))
        return True

    def _take_design_drift(self, call_id: str) -> list[Any]:
        """The drift `_check_drift` kept for this call, once."""
        return self.__dict__.get("_design_drift", {}).pop(call_id, None) or []

    def _log_design_drift(self, tool_call: ToolCall, drifts: list[Any]) -> None:
        """One `design/drift` row per write that moved a reviewed design's tokens."""
        self.log.append(
            "design/drift",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            pages=sorted({drift.page for drift in drifts}),
            tokens=len(drifts),
        )

    def _take_design_reports(self, call_id: str) -> Optional[list[Any]]:
        """The reports `_check_written_designs` kept for this call, once."""
        return self.__dict__.get("_design_reports", {}).pop(call_id, None)

    def _log_design_checks(self, tool_call: ToolCall, reports: list[Any]) -> None:
        """One `design/check` row per page checked, with the counts the harness review reads. The
        text the model read is in the write's own `tool/result` row."""
        for report in reports:
            self.log.append(
                "design/check",
                turn=self._turn_no,
                step=self._step_no,
                call_id=tool_call.id,
                name=tool_call.name,
                path=report.shown,
                facts=report.facts(),
            )
