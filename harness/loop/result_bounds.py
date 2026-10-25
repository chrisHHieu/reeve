"""ResultBoundsMixin: bounding one tool result for the model's window."""

from __future__ import annotations

import json
from typing import Any, Optional

from harness.llm.providers import ToolCall
from harness.tools.contract import FULL_CHARS, LOCATOR, SHOWN_CHARS
from harness.tools.retention import RESULT_CEILING_CHARS, RESULT_CEILING_LINES, retain

from .limits import SHRUNK_RESULT_BUDGET, STEP_OUTPUT_BUDGET
from .nudges import truncation_hint


def _spill(store: Any, text: str) -> Optional[str]:
    """Write the complete text through the session's spill store, or None if that fails.

    `_enforce_turn_budget` spills before it splits, so the hint's path length is known and can be
    budgeted inside the ceiling, which is why this is not just `retention._save`. The failure rule is
    the same one: a full disk or a revoked root costs the locator and never the turn."""
    try:
        return store.save(text, label="turn") or None
    except Exception:
        return None


class ResultBoundsMixin:
    def _enforce_turn_budget(
        self, tool_call: ToolCall, rendered: Any, result: Any, facts: Optional[dict[str, Any]] = None
    ) -> tuple[Any, Any]:
        """Bound one tool result for the model's window, in two tiers.

        The per-result ceiling is the outer bound. No single result may take more than
        `RESULT_CEILING_CHARS` of the next request, whatever produced it, and none may be a wall of
        more than `RESULT_CEILING_LINES` lines. The built-in tools each cap their own output (shell
        20k, web fetch 20k, read 50KB), but MCP and connector tools are third-party code and some
        return whatever their server sends. A single 5 MB JSON blob used to sail into the window
        because the aggregate budget below starts counting only once a turn has crossed 120k.

        The aggregate budget is the bound at the level of a step (`STEP_OUTPUT_BUDGET`, reset by
        `_handle_tool_calls`). The per-tool caps bound one result, but the batch of medium results in
        one response adds up to the same request. Only the overshoot is paid for: the newest
        model-visible text keeps a small head and a spill locator, and the repeat guard has already
        seen the full text, so a shrunk and an unshrunk copy of one answer count as the same work. A
        self-bounded result (`read_file`) counts toward the total and is never shrunk by it, because
        it is how the model reads a spill back and shrinking it would leave no way to recover
        anything. Without a contract render the model reads the JSON of the result, and that is the
        text the budget applies to.

        There is one file per result. A tool that cut its own output has already written
        the complete text and says where (`facts["locator"]`), and a second cut here names that file
        and does not write the already-cut text to another. It used to: one `web_fetch` came back with
        `page-8.log` (the page) and `turn-9.log` (the cut page), and the model took the second for a
        turn log. `facts` is updated with what the model was finally shown."""
        if rendered is not None and not isinstance(rendered, str):
            return rendered, result
        if isinstance(rendered, str) and rendered:
            text = rendered
        else:
            text = json.dumps(result, default=str) if rendered is None else None
        if not text:
            return rendered, result
        self._step_output_chars += len(text)
        contract = self.registry.contract(tool_call.name)
        self_bounded = bool(getattr(contract, "self_bounded", False))
        # Past the aggregate budget everything new shrinks to the hard stub, except a self-bounded
        # result. Below it, only a single result that outgrows the outer ceiling is touched.
        limit = (
            SHRUNK_RESULT_BUDGET
            if self._step_output_chars > STEP_OUTPUT_BUDGET and not self_bounded
            else RESULT_CEILING_CHARS
        )
        lines = text.count("\n") + 1
        if len(text) <= limit and lines <= RESULT_CEILING_LINES:
            return rendered, result
        # The hint is part of the model-facing text, so it is budgeted before the split: the
        # retained text plus the hint must fit the ceiling (retention.py's contract is that a bound
        # never quietly returns more than it promised). The complete text is written first, so the
        # hint names a path whose length is already known. The same locator goes back to `retain`
        # through a no-op sink, so its marker keeps the "complete output is at …" path that the
        # compaction fold reads.
        own = facts.get(LOCATOR) if facts else None
        saved: Optional[str] = own if isinstance(own, str) and own else None
        if saved is None and self.spill is not None:
            saved = _spill(self.spill, text)
        declared = facts.get(FULL_CHARS) if facts else None
        full = max(len(text), declared if isinstance(declared, int) else 0)
        hint = truncation_hint(self.registry.get("task") is not None, saved, full)
        budget = limit - len(hint) - 2  # the blank line between the text and the hint
        retained = retain(
            text,
            budget=max(1, budget),
            spill=(lambda _text: saved) if saved else None,
            # The line ceiling applies in the same cut, since a wall of short lines fits any
            # character budget, less the lines the hint and its separator take.
            max_lines=RESULT_CEILING_LINES - hint.count("\n") - 2,
        )
        if retained.text == text:
            # Unreachable by construction, since the branch above proved a cap was exceeded, but a
            # bounded promise is kept defensively: what came in is handed back.
            return rendered, result
        kept = f"{retained.text}\n\n{hint}"
        self._step_output_chars += len(retained.text) - len(text)
        self.log.append(
            "tool/result/budget",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            limit=limit,
            kept=len(kept),
            omitted=retained.omitted_chars,
            spilled=saved,
            # The file is the tool's own: this bound wrote nothing.
            reused=True if own and saved == own else None,
        )
        if facts is not None:
            if saved:
                facts[LOCATOR] = saved
            facts[FULL_CHARS] = full
            facts[SHOWN_CHARS] = len(kept)
        if rendered is not None:
            return kept, result
        return None, kept
