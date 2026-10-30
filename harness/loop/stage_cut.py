"""StageCutMixin: the two cuts made before the window's edge.

A stage compaction summarizes a stage of the work where it just ended verified, instead of wherever
the window fills; the working trim takes aged output of re-runnable calls out of the view while the
cache is warm, in batches large enough to repay the cache they cost. Both go through the one
compaction transaction in `compaction.py`: the stage cut is a `_compact_now` the trigger
check let through early, the trim a prune adopted like every other.
"""

from __future__ import annotations

from harness.context import compaction


class StageCutMixin:
    def _stage_due(self, signal: int, trigger: int) -> bool:
        """Whether a stage of the work just ended with the context past half the trigger: the
        step that just finished ran a check that passed on everything edited before it, in a turn
        that wrote code. That is where a summary loses least (AutoCompact, arXiv 2610.02163:
        compaction tied to length rather than progress fired mid-stage and cost 1.6 points on
        SWE-bench Verified, compaction at a resolved stage gained), so the session is compacted
        there instead of at the window's edge. `[compaction] stage_compaction = false` turns it off."""
        if not self._compaction_config().get("stage_compaction", True):
            return False
        if signal < compaction.STAGE_SHARE * trigger or not getattr(self, "_turn_wrote_code", False):
            return False
        from .verification import last_verification

        check = last_verification(self.messages)
        if check is None or check.ok is not True or check.edits_since != 0:
            return False
        newest = next((i for i in range(len(self.messages) - 1, -1, -1) if self.messages[i].get("role") == "tool"), -1)
        if check.index != newest:
            return False
        # The finished stage must be worth a summary and the cache it costs. Measured 2026-10-10 on
        # the long session: a stage cut that kept the last `keep_tokens` kept the stage itself and
        # took 4-5k off a 36-40k context, and the threshold cut came two steps later anyway.
        start = self.compaction_state.boundary_index if self.compaction_state is not None else 0
        span = compaction.estimate_tokens(self.messages[start:], echo=self._reasoning_echo())
        return span >= compaction.STAGE_MIN_SHARE * trigger

    def _prunes_rederivable(self) -> bool:
        """Whether this engine's prune drops the aged output of calls that can be made again
        (`compaction/rederive.py`): on unless the engine or `[compaction] prune_rederivable`
        says false."""
        override = getattr(self, "prune_rederivable", None)
        if override is not None:
            return bool(override)
        return bool(self._compaction_config().get("prune_rederivable", True))

    def _working_trim(self) -> bool:
        """Drop aged re-runnable output while the cache is warm, in batches large enough to pay
        for the cache they cost.

        A cut rewrites the middle of the prompt, so the next request reads nothing from the cache
        after it and is priced in full once. It is worth that when what it removes is large: at a
        quarter of the request, the saving on every later read covers the one full-price request
        within about ten to twenty-five steps on the providers' prices (cached input at a tenth to a
        quarter of fresh), and long sessions run hundreds. The working-window prune was
        removed because it bought 12-15% (aged results folded to their ends); this tier removes
        whole outputs, which in long sessions are about half the history. Logged as
        `compaction/prune` with `reason: rederivable`."""
        if not self._prunes_rederivable():
            return False
        signal = self._context_signal()
        if not signal or signal < 2 * compaction.TRIM_MIN_TOKENS:
            return False
        result = self._prune_candidate(self._history_trigger_tokens())
        if result is None:
            return False
        candidate, upto, before, after, folded = result
        if before - after < max(compaction.TRIM_MIN_TOKENS, compaction.TRIM_MIN_SHARE * before):
            return False
        old_upto = self.compaction_state.pruned_upto if self.compaction_state is not None else 0
        arguments = self._arguments_folded(candidate, old_upto, upto)
        self.compaction_state = candidate
        self._forget_context_measure()
        self.log.append(
            "compaction/prune",
            turn=self._turn_no,
            step=self._step_no,
            reason="rederivable",
            before_tokens=before,
            after_tokens=after,
            saved_tokens=before - after,
            pruned_upto=upto,
            folded=folded or None,
            arguments_folded=arguments or None,
            images_offloaded=len(candidate.offloaded_images) or None,
        )
        return True
