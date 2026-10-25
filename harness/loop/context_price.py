"""ContextPriceMixin — what a context costs, in one unit.

Every decision about the window reads a number of tokens: is compaction due, did the prune
alone get under the trigger, what would a fold free, what did a compaction save. Those
numbers were produced four ways — the provider's count, a chars/4 estimate of the request,
a chars/4 estimate of the STORED rows, and that last one times a ratio learnt by dividing
the provider's count for the request by the estimate of the stored rows. The last two
describe text no provider is sent (thinking, cards, a second copy of every stamped
message), and after a compaction the ratio's two halves were not even the same span.

Real store, 2026-10-04: at 20 compactions the harness's price of its own view sat within
20% of the provider's once (0.28x to 2.35x). The estimate of the request AS SENT, which the
context panel already took, was within 0.8..1.25 of the provider on 6,306 of 6,403
requests with no correction at all, and on 6,250 of 6,268 with one factor per session.

So there is one price: chars/4 over the request as it is sent (wire messages, system
prompt, tool schemas), times the bias the provider's last count showed for that same
request. `_view_tokens` gives it for a whole view; `_context_signal` is the provider's own
count plus that price for what was appended since.
"""
from __future__ import annotations

import logging

import json
from harness.context import compaction
from typing import Any

from .outbound import CURRENT_STATE

#: Growth, in estimator tokens, past which a provider's prompt count must have moved:
#: below it a tokenizer difference or a trimmed reasoning block can hide the change.
_USAGE_CHECK_MIN_GROWTH = 2_000
#: How far a provider's count may sit from the estimate of the same request and still be
#: read as the estimator's bias. On the real store 98% of requests are inside 0.8..1.25;
#: past half or double, the number is saying something else (a partial usage report, an
#: attachment priced by the page) and is not carried onto the next estimate.
_BIAS_RANGE = (0.5, 2.0)

logger = logging.getLogger(__name__)


class ContextPriceMixin:
    def _tool_schema_tokens(self) -> int:
        """The schemas this request advertises, estimated: they occupy the window like any
        message, and about 11k tokens of every request in the real store are these."""
        return sum(
            len(json.dumps(schema, default=str, ensure_ascii=False)) // 4
            for schema in self._model_tools()
        )

    def _priced(self, estimate: int) -> int:
        """An estimate in the provider's unit: times the bias its last count showed."""
        return int(estimate * (self._estimate_bias or 1.0))

    def _view_tokens(self, state: Any = CURRENT_STATE) -> int:
        """The price of the request a compaction state would produce — the session's own
        by default, or one being weighed.

        The view is the REAL one: `_outbound_messages` applies a staged revert, suspends a
        state that does not fit it, and puts each message in wire form, so the number is
        for the bytes a provider would receive. Tool schemas are part of it: a comparison
        with the trigger, or with the provider's count, is a comparison of whole requests.
        """
        return self._priced(
            compaction.estimate_tokens(self._outbound_messages(state))
            + self._tool_schema_tokens()
        )

    def _context_signal(self) -> int:
        """Tokens currently occupying the window.

        The provider's own number for the last request, PLUS the price of everything
        appended since it — the assistant turn it produced and every tool result of this
        step. Without that addend the signal is one whole step stale, so a single large
        result could sail past the threshold and overflow on the next request; the
        overflow path recovers, but it recovers by spending a failed request first.

        Falls back to pricing the whole view when the provider never reported usage, or
        when its last count no longer describes the view (a compaction, a model switch).
        """
        if self._last_context_tokens is None:
            return self._view_tokens()
        since = self.messages[self._context_measured_at :]
        return self._last_context_tokens + self._priced(
            compaction.estimate_tokens(since, echo=self._reasoning_echo())
        )

    def _record_context_measure(self, measured: int) -> None:
        """Take the provider's prompt size for the request that just ran — unless it
        cannot be the whole prompt.

        History is append-only between measurements (a compaction, prune or revert resets
        `_last_context_tokens`), so a request made after thousands more tokens were
        appended must price at least most of them. opencode-go's Anthropic-wire route for
        qwen3.8-flash reported `input=12209, cache_read=0` five steps running while the
        session grew (harness eval, 2026-09-24): trusted, the signal never passed 13k and
        compaction could never fire — the session would run into the model's hard limit.
        Once a provider is caught under-reporting, the engine stops believing it for the
        rest of the session and prices the view itself.
        """
        previous = self._last_context_tokens
        # The estimate of the request the provider just priced: nothing has been appended
        # since it was built (the assistant message lands after this), so the view now is
        # the request then.
        view = (
            compaction.estimate_tokens(self._outbound_messages())
            + self._tool_schema_tokens()
        )
        before = self._measured_view_tokens
        if not self._usage_unreliable and previous is not None and before is not None:
            grown = view - before
            if grown >= _USAGE_CHECK_MIN_GROWTH and measured < previous + grown * 0.25:
                self._usage_unreliable = True
                logger.warning(
                    "provider usage for %s looks partial (%d tokens after %d, with ~%d "
                    "appended); estimating the context instead",
                    self.model, measured, previous, grown,
                )
                # Durable too: the Metrics tab lists the routes caught under-reporting.
                self.log.append(
                    "notice",
                    turn=self._turn_no,
                    step=self._step_no,
                    kind="usage_partial",
                    model=self.model,
                    text=(
                        f"{self.model} priced this request at {measured:,} tokens after "
                        f"{previous:,}, with ~{grown:,} appended; estimating the context"
                    ),
                )
        if self._usage_unreliable:
            self._last_context_tokens = None
            return
        self._last_context_tokens = measured
        self._context_measured_at = len(self.messages)
        self._measured_view_tokens = view
        # Like for like: the provider's count over the estimate of the SAME request. The
        # ratio this replaces divided by an estimate of the stored rows, which a
        # compaction turns into a different span altogether.
        if view > 0 and measured > 0:
            low, high = _BIAS_RANGE
            observed = measured / view
            if low <= observed <= high:
                self._estimate_bias = observed
