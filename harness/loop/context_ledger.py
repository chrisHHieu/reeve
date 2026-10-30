"""ContextLedgerMixin — the model is shown what its context is made of.

Off in the product until the ablation says it pays (`[compaction] context_ledger`,
`benchmarks.ablation --enable context_ledger`). When on, a step that leaves enough foldable
context gets a short `<context-ledger>` note after its results: the total, what a fold would
replace, and the heaviest results still in view with their age. It is a fact, not a request:
the fold hints still ask at work boundaries, and the ledger is what lets a
model decide between them.

It is appended to the history like the fold hint, so the request stays the previous one plus an
append (a provider's prefix cache is kept, and so is a Claude thinking block bound to its
prefix), and it is logged as a `context/budget` row (`hint: "ledger"`) that rebuilds it (0001).
Spaced (`LEDGER_EVERY` steps) so its own tokens stay small against what it reports.
"""

from __future__ import annotations

import time
from typing import Any

from harness.context import ledger as _ledger
from harness.session.turns import ATTACHED_BY
from harness.tools.context_fold import FOLD_CONTEXT

#: Steps between two ledgers in one stretch (a stretch ends at a cut). About a hundred tokens
#: each; a stretch on the store ran a median 25 steps, so about six.
LEDGER_EVERY = 4
LEDGER_TAG = "context_ledger"


class ContextLedgerMixin:
    #: Off unless the session config or an ablation turns it on.
    context_ledger: bool = False

    def _maybe_context_ledger(self) -> None:
        """After a step: the ledger, when it is on, folding is offered, enough could be folded,
        no fold hint was just given, and the last ledger of this stretch is `LEDGER_EVERY` steps
        old."""
        if not self.context_ledger or not getattr(self, "model_fold", False):
            return
        if self.registry.get(FOLD_CONTEXT) is None or self.model in self._fold_cache_lost:
            return
        if self.messages and self.messages[-1].get(ATTACHED_BY) == "fold_hint":
            return  # the hint already says it, with the same numbers
        state = self.compaction_state
        boundary = state.boundary_index if state is not None else 0
        steps = getattr(self, "_ledger_steps", 0) + 1
        self._ledger_steps = steps
        last = getattr(self, "_ledger_last", None)
        if last is not None and last[0] == boundary and steps - last[1] < LEDGER_EVERY:
            return
        used = self._context_signal()
        foldable = self._foldable_tokens(used)
        if foldable < self._fold_hint_tokens():
            return
        start = boundary or (1 if self.messages and self.messages[0].get("role") == "system" else 0)
        built = _ledger.build(
            self.messages, start, used=used, trigger=self._history_trigger_tokens(), foldable=foldable
        )
        if built is None:
            return
        text, row = built
        self._ledger_last = (boundary, steps)
        self.messages.append({"role": "user", "content": text, ATTACHED_BY: LEDGER_TAG, "ts": time.time()})
        # Model-visible means logged (0001): the text is rebuilt from these numbers.
        self.log.append("context/budget", hint="ledger", moment="step", turn=self._turn_no, step=self._step_no, **row)

    def ledger_row(self, row: dict[str, Any]) -> str:
        """The ledger a logged row stands for, for the audit and the tests."""
        return _ledger.render(
            used=int(row.get("used") or 0), trigger=int(row.get("trigger") or 0),
            foldable=int(row.get("foldable") or 0), items=list(row.get("items") or []),
        )


__all__ = ["LEDGER_EVERY", "LEDGER_TAG", "ContextLedgerMixin"]
