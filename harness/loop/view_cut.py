"""ViewCutMixin — what a cut of the context may not cost.

A compaction and a fold both replace older turns with a block. Three things the harness
knows about the work must keep being true across that cut, and none can be left to a note:

- **What the agent has seen follows what it can still see.** The read-before-edit store
  (`tools/observed.py`) rests on "the read is still in the model's context". A cut is the
  moment that stops being true, so every adoption of a compaction state — the summarizer's,
  a fold, a prune, a restore on resume — tells the store which files are still in the view.
  A read the prune shortened, or a write whose payload it folded, no longer
  shows the file. A whole-file write of any other file needs a fresh read (`out-of-view`).
  An edit that quotes the text it replaces needs nothing: written from memory it fails to
  match.
- **What the harness has stated follows what the request still states.** A nested
  instruction file or a path-scoped rule is stated once, on the turn-start message that
  first needed it. A cut that puts that message behind the block has taken the statement
  out of the request, so it is owed again for the directories still being worked in
  (`_conventions_in_view`, the same rule the live-context block already follows).
- **The harness does not ask for a fold in the middle of an edit it cannot yet call
  verified.** Its own completion gates name that state: code changed in
  this turn and nothing ran since, or the turn's last check failed and nothing changed
  since. The model may still fold; a fold made there is logged with the state it was made
  in (`unsettled` on `compaction/start`), which is the count a refusal would be decided on.
"""
from __future__ import annotations

import logging

import json
from harness.context import compaction
from harness.safety.permissions import write_paths
from harness.session.turns import is_user_turn
from typing import Optional

from harness.session import revert as _revert
from harness.loop.call_pairs import answered_calls
from harness.tools.observed import READ_TOOLS
from harness.loop.verification import last_verification
from harness.llm.wire import CONVENTIONS

logger = logging.getLogger(__name__)


class ViewCutMixin:
    @property
    def compaction_state(self) -> Optional[compaction.CompactionState]:
        return self.__dict__.get("_compaction_state")

    @compaction_state.setter
    def compaction_state(self, state: Optional[compaction.CompactionState]) -> None:
        """Every way a state is adopted lands here (a compaction, a fold, a restore), so
        the read-before-edit store cannot be left believing in a view that is gone."""
        self.__dict__["_compaction_state"] = state
        try:
            self._view_follows_cut(state)
        except Exception:  # noqa: BLE001 — bookkeeping must never cost an adoption
            logger.debug("observed view not updated after a cut", exc_info=True)

    def _view_follows_cut(self, state: Optional[compaction.CompactionState]) -> None:
        observed = getattr(self, "observed", None)
        if observed is None:
            return
        boundary = max(0, state.boundary_index if state is not None else 0)
        upto = state.pruned_upto if state is not None else 0
        if boundary <= 0 and upto <= 0:
            observed.leave_view(None)  # nothing cut or shortened: everything seen is there
            return
        messages = list(getattr(self, "messages", []))
        tail = messages[boundary:]
        call_at = {
            id(call): boundary + offset
            for offset, message in enumerate(tail)
            if message.get("role") == "assistant"
            for call in message.get("tool_calls") or []
        }
        kept: list[str] = []
        for offset, message, call in answered_calls(tail):
            if message.get("role") != "tool" or call is None:
                continue
            result = message.get("content")
            if not isinstance(result, str) or result.lstrip().startswith(("Error", "tool call blocked")):
                continue  # a read that failed showed nothing; a write that failed wrote nothing
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            argument = READ_TOOLS.get(name)
            if argument is not None:
                rederive = bool(state is not None and state.prune_rederivable)
                if boundary + offset < upto and compaction.result_shortened(result, name, rederive):
                    continue  # the view holds its two ends, not the file
                if args.get(argument):
                    kept.append(str(args[argument]))
                continue
            if (
                state is not None
                and state.prune_arguments
                and call_at.get(id(call), len(messages)) < upto
                and compaction.fold_arguments(name, args) is not None
            ):
                continue  # the view says a write happened, not what it wrote
            paths, located = write_paths(name, args)
            if located:
                kept.extend(str(path) for path in paths)
        observed.leave_view(kept)

    def _conventions_in_view(self) -> tuple[set[str], set[str]]:
        """`(nested instruction files, path-scoped rules)` the request still states.

        Each turn-start stamp records what it added on the message that carries it
        (`wire.CONVENTIONS`). A message behind the compaction boundary is represented by the
        block, which does not carry the stamp; one inside a staged undo is hidden. Neither
        is in the request, so what they stated is owed again. Read from the thread on every
        pass and never kept: a set beside the thread was right until the first cut, and
        empty again in an engine rebuilt over the same thread.
        """
        messages = list(getattr(self, "messages", []) or [])
        state = self.compaction_state
        start = state.boundary_index if state is not None else 0
        marker = getattr(self, "revert_state", None)
        hidden_from = _revert.marker_boundary(marker)
        hidden_to = (hidden_from or 0) + _revert.hidden_count(marker)
        nested: set[str] = set()
        rules: set[str] = set()
        for index in range(max(start, 0), len(messages)):
            if hidden_from is not None and hidden_from <= index < hidden_to:
                continue
            stated = messages[index].get(CONVENTIONS)
            if not isinstance(stated, dict):
                continue
            nested.update(str(key) for key in stated.get("nested") or ())
            rules.update(str(key) for key in stated.get("rules") or ())
        return nested, rules

    def _work_unsettled(self) -> str:
        """`unverified` / `check_failed` while this turn holds an edit the harness cannot
        yet call verified, else "". The same two facts the completion gates act on."""
        if getattr(self, "_turn_wrote_code", False) and not getattr(self, "_turn_ran_after", False):
            return "unverified"
        check = last_verification(self.messages)
        if check is None or check.ok is not False or check.edits_since != 0:
            return ""
        turn_start = next(
            (i for i in range(len(self.messages) - 1, -1, -1) if is_user_turn(self.messages[i])), 0
        )
        return "check_failed" if check.index >= turn_start else ""
