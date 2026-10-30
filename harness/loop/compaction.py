"""CompactionMixin — auto-compaction: when the context is summarized, and the durable transaction
that records it."""
from __future__ import annotations

import logging

import asyncio
import time
from .message_shapes import log_text
from dataclasses import replace
from harness.context import compaction
from harness.llm.providers import ToolCall
from harness.session.turns import TurnEndReason
from typing import Any, Optional

import hashlib

from harness.session import revert as _revert
from harness.session.turn_numbers import last_turn

#: How many pruned-result spill paths the content-addressed sink remembers. The prune
#: tier renders on every request, so the sink must write a given text once; a bound keeps
#: a very long session from growing the map forever (a cleared map costs one rewrite).
_SPILL_CACHE_MAX = 128


def _short_failure(failure: str) -> str:
    """The summarizer's error as one short line for a person: its first line, clipped."""
    line = (failure or "").strip().splitlines()[0] if (failure or "").strip() else ""
    return line if len(line) <= 160 else line[:157] + "..."

logger = logging.getLogger(__name__)


class CompactionMixin:
    # -- auto-compaction ------------------------------------------------
    def _compaction_config(self) -> dict[str, Any]:
        cfg = dict(self.compaction_settings() or {}) if self.compaction_settings else {}
        if not cfg.get("context_window"):
            from harness.llm.providers.matrix import model_context_windows

            cfg["context_window"] = model_context_windows().get(self.model)
        # A catalogue window is not a budget. `max_context_tokens` clamps the window the
        # trigger is computed from: a number is obeyed exactly (0 = the model's window is
        # the only bound), and `"auto"`, the default, is the same for every model — its
        # own window, never more than `AUTO_ACCURACY_CEILING`, past which the audited
        # sessions' tool-error rate doubled. No price enters it.
        cap_setting = cfg.get("max_context_tokens")
        if isinstance(cap_setting, str) and cap_setting.strip().lower() == "auto":
            cap = compaction.AUTO_ACCURACY_CEILING
        else:
            try:
                cap = int(cap_setting or 0)
            except (TypeError, ValueError):
                cap = 0
        capped_window = 0
        if cap > 0:
            window = int(
                cfg.get("context_window") or compaction.DEFAULT_CONTEXT_WINDOW
            )
            if cap < window:
                cfg["context_window"] = cap
                cfg["uncapped_window"] = window
                capped_window = cap
        # The trigger: `window − max(output, buffer)`. No threshold fraction, no
        # absolute cap — the (possibly capped) window is the budget.
        cfg.setdefault("buffer_tokens", compaction.DEFAULT_BUFFER_TOKENS)
        cfg.setdefault("keep_tokens", compaction.DEFAULT_KEEP_TOKENS)
        if capped_window:
            # A capped window needs a proportional keep: the fixed default is sized for
            # uncapped 600k+ windows, and keeping 8k of a 50k cap makes the model redo
            # work (measured 2.15x cost on a read-heavy task). The configured keep still
            # wins when it is larger.
            cfg["keep_tokens"] = compaction.capped_keep_tokens(
                int(cfg.get("keep_tokens") or 0), capped_window
            )
        # The model's own response budget, so the trigger leaves room for the answer
        # (compaction.trigger_tokens). Explicit request settings win; else the catalogue
        # (models.dev `limit.output`); else unknown, which leaves the buffer as the reserve.
        if not cfg.get("output_tokens"):
            output_tokens = 0
            setting = self.model_settings.get("max_tokens")
            if isinstance(setting, int) and setting > 0:
                output_tokens = setting
            else:
                try:
                    from harness.llm.providers.matrix import model_details

                    output_tokens = int(model_details(self.model).get("output") or 0)
                except Exception:
                    output_tokens = 0
            cfg["output_tokens"] = output_tokens
        return cfg

    def _compaction_due(self) -> bool:
        """The trigger check alone — cheap and side-effect free, so the loop can emit
        the COMPACTING signal before committing to the (slow) summarizer call.

        (There used to be an `enabled is False` gate here. Nothing in the product ever
        set that key — not Settings, not prefs, not a test — so it was a switch that
        looked configurable and was not. A real off switch would guarantee that any long
        session dies of context overflow, so the honest fix was to delete the branch
        rather than wire it.)
        """
        return bool(self._compaction_why())

    def _compaction_why(self) -> str:
        """Why a compaction is due now: `full` (the context reached the trigger), `stage` (a stage
        of the work just ended past half of it, `stage_cut.py`), or "" when none is. A surface says
        it while the summarizer runs: "Compacting" alone, beside a gauge at a third,
        read as a gauge that had not caught up."""
        signal = self._context_signal()
        trigger = self._history_trigger_tokens()
        if signal >= trigger:
            return "full"
        return "stage" if self._stage_due(signal, trigger) else ""

    async def _compact_now(
        self, *, force: bool = False, manual: bool = False
    ) -> Optional[compaction.CompactionNotice]:
        """Run the compaction policy. Callers gate on `_compaction_due()` (or `force`,
        the overflow path). Returns what the attempt did — the sentence for the model, the
        numbers for whoever is watching — or None when the outbound view did not change.
        Failure policy per spec: retry once (both modes); an attended session is asked
        Retry / Skip, an unattended one records the failure and keeps the view exactly as
        it was. There is no trim fallback (study round 29): a failed summary costs time and
        money, never content."""
        cfg = self._compaction_config()
        trigger = self._history_trigger_tokens()
        # `keep_tokens`: an absolute number of recent tokens kept verbatim, not a
        # fraction of the window (a fraction made a 1M model keep a quarter megatoken of raw
        # transcript — the whole point of compacting it was to spend less than that).
        keep_cfg = cfg.get("keep_tokens")
        keep = (
            compaction.DEFAULT_KEEP_TOKENS if keep_cfg is None else int(keep_cfg)
        )
        model = str(cfg.get("model") or "") or self.model
        # A stage compaction (`_stage_due`: due below the trigger, at a stage the work just
        # finished) cuts at the stage's end: the finished stage is summarized and only the step that
        # checked it stays verbatim, beside the harness record of that check (`witnessed.py`).
        # Keeping the usual tail would keep the stage itself.
        stage = not force and not manual and self._context_signal() < trigger
        if stage:
            keep = 0

        # The anti-thrash gate, and the only memory this policy has of its own attempts.
        # `_compaction_due()` is a pure reading of pressure, so without this a session
        # compaction cannot shrink re-attempts on EVERY iteration — a summarizer
        # round-trip per step, which is how a context problem becomes a bill.
        #
        # `force` skips it, and that is deliberate: force means the provider already
        # refused the request on window size, so declining to try guarantees the turn dies.
        # A cooldown is for "this is not working", not for "this must not fail".
        # The cooldown is a policy about automatic attempts ("this is not working");
        # a person asking (`manual`) and a provider that already refused (`force`) are
        # not that, so both skip it. A manual request also runs below pressure — that
        # is the entire point of a `/compact`.
        if not force and not manual:
            allowed, why = self._compaction_gate_now()
            if not allowed:
                logger.info(
                    "compaction held back (%s); the last attempt was not productive", why
                )
                return None

        # The replay is a snapshot of the request that just ran, so it has to be taken
        # BEFORE the cheap lever rewrites anything: `_prune_now` below shortens tool results
        # in the middle of the prompt, and a provider matches a cached prefix byte for byte.
        # Snapshotted afterwards, the replay would diverge at the first pruned result and
        # everything after it would be re-priced — the replay paying full price for the very
        # conversation it exists to reuse, which is the cost the two gates below are for.
        # Snapshotted here it keeps every EARLIER prune, because those were already on the
        # wire for the previous request.
        #
        # First gate, cheap and certain: a summarizer on a DIFFERENT model has no cache of
        # this conversation to read, so the replay would be strictly more expensive.
        replay = self._outbound_messages() if model == self.model else None
        # Second gate: whether the cache actually answers. Nothing here can prove a hit in
        # advance — the prefix has to match byte for byte, and the provider decides. So ask
        # once and believe the answer, the same way the Bedrock cache point does.
        if not self._summarizer_replay_pays:
            replay = None

        # The attempt is a durable transaction from here (compaction/start →
        # summary/end): written once the gate above has decided this attempt is real,
        # bracketed by an end on every exit below, and repaired synthetically by
        # `_close_dangling_turn` if the process dies in between. Without it, a session
        # that compacted has a display notice and nothing an auditor can read; the
        # model-visibility invariant (invariants.py) requires the durable half.
        self.log.append(
            "compaction/start",
            turn=self._turn_no,
            step=self._step_no,
            trigger=trigger,
            keep_tokens=keep,
            model=model,
            before_tokens=self._view_tokens(self.compaction_state),
            # The number the trigger actually compared: the provider's last count plus the
            # delta since. `before_tokens` is the price of the same view taken whole;
            # the two are one unit, and `harness_review` counts the rows
            # where they disagree.
            signal=self._context_signal(),
            force=True if force else None,
            manual=True if manual else None,
            replay=True if replay is not None else None,
        )

        # The cheap lever, on BOTH paths. Aged tool results keep their two ends; if that
        # alone brings the window under the trigger, the summarizer is never called — no
        # round-trip, no latency, and nothing summarized away that a re-read could
        # recover. Only ever moves forward, so a result is rewritten at most once and the
        # cached prefix settles instead of drifting.
        #
        # Why `force` runs it too: force means the provider already refused the request
        # for length, which is the moment paying for a summarizer is LEAST affordable —
        # the summarizer's replay is the same over-window conversation, so it may be
        # refused as well, and pruning is the only lever that can make the replay fit.
        # Pruning also rewrites fewer bytes than a summary does, so it invalidates less
        # of the prefix the provider had cached. It used to be skipped here, which meant
        # the one path that had to shrink the context jumped straight to the expensive
        # and possibly-impossible move.
        #
        # `manual` is the one path that skips the cheap lever: a person pressing
        # "compact now" is asking for the summary, and a prune-only outcome would answer
        # a different request. Aged results inside the summarized span fold into the
        # summary anyway.
        #
        # A stage compaction skips it too: it was called to summarize the finished stage, and a
        # prune that brings a view already under the trigger "under" it would answer something else.
        if not manual and not stage:
            pruned = self._prune_now(trigger)
            if pruned is not None:
                return pruned

        summary_usage: list[Any] = []

        def _saw_usage(usage: Any) -> None:
            # Every summarizer round-trip is recorded, cached ones included: it is
            # provider traffic the turn caused, and compaction fires exactly when the
            # context is at its fullest — the moment a replay-priced call is largest.
            if usage is not None:
                summary_usage.append(usage)
            if replay is None or usage is None:
                return
            if not getattr(usage, "cache_read", 0):
                # The replay sent the whole conversation and was billed for all of it.
                # Once is a measurement; twice would be a habit.
                self._summarizer_replay_pays = False

        def _build() -> Optional[compaction.CompactionState]:
            return compaction.build_state(
                self.messages,
                provider=self.provider,
                model=model,
                keep_tokens=keep,
                prior=self.compaction_state,
                replay=replay,
                tools=self.registry.schemas() if replay is not None else None,
                usage_sink=_saw_usage,
                trigger=trigger,
                standing_plan=self._standing_plan(),
                approved_plan=self.approved_plan,
                plan_path=str(self.plan_path) if self.plan_path is not None else "",
                # Custom instructions REPLACE the default summarizer prompt (Anthropic's
                # rule). The
                # code-level net — redaction, mechanical block, verbatim user messages —
                # holds either way, so a custom prompt can only change what the LLM
                # emphasizes, not what the harness guarantees.
                instructions=str(cfg.get("instructions") or ""),
                output_limit=int(cfg.get("output_tokens") or 0) or None,
            )

        state: Optional[compaction.CompactionState] = None
        failed = False
        failure = ""
        for _attempt in range(2):  # first try + the unconditional single retry
            try:
                state = await asyncio.to_thread(_build)
                failed = False
                failure = ""
                break
            except Exception as exc:
                failed = True
                # Kept for the durable record: without it a harness bug and a provider
                # outage look identical (the retry loop below writes it to the log).
                failure = f"{type(exc).__name__}: {exc}"
        if failed and self.question_asker is not None and self.is_attended and self.is_attended():
            while True:
                answer = await self._interruptible(
                    self.question_asker(
                        {
                            # The reason rides the question: it used to reach the log only
                            # after the person answered, so the prompt said "failed" and
                            # nothing anyone could act on (a timeout and a refused request
                            # call for different answers).
                            "question": (
                                "Context compaction failed — the summarizer couldn't "
                                "condense this session's history"
                                + (f" ({_short_failure(failure)})" if failure else "")
                                + ". How should I proceed?"
                            ),
                            # No trim anymore (study round 29): a failed summary leaves the
                            # history exactly as it was, so the only real choices are "try
                            # again now" and "keep going as is".
                            "options": ["Retry", "Skip for now"],
                            "allow_text": False,
                            "header": "Compaction",
                        },
                        None,
                    ),
                    interrupted=None,
                )
                if not answer or answer.get("answer") != "Retry":
                    break
                try:
                    state = await asyncio.to_thread(_build)
                    failed = False
                    break
                except Exception as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    continue
        # The summarizer's own round-trip counts as this turn's spend exactly like the
        # main model's. It used to be invisible: `_turn_tokens` only saw assistant
        # usage, so a session oscillating around the trigger could pay for repeated
        # full-context summarizer calls the token budget never saw. Charged once here,
        # from every usage the attempt reported (a retried summarizer paid each time).
        summarizer_tokens = sum(getattr(u, "total", 0) for u in summary_usage)
        self._turn_tokens += summarizer_tokens

        if state is not None:
            return self._adopt_compaction(
                state,
                "summarized",
                "Context compacted — earlier turns were summarized",
                summarizer_tokens=summarizer_tokens,
                manual=manual,
            )
        if failed:
            # Every attempt failed and there is no trim fallback (study round 29): the
            # history stays exactly as it was, the reason is durable, and the gate waits
            # instead of paying for the same failure on the next iteration. A session that
            # cannot compact will eventually hit its provider's context limit and fail
            # loudly there — which is better than silently dropping turns.
            logger.warning("compaction summarizer failed (%s)", failure or "unknown error")
            self._last_compaction_error = failure or "unknown error"
            self._stamp_compaction_failure()
            self._log_compaction_end(
                "failed", summarizer_tokens=summarizer_tokens, error=failure
            )
            return None
        # No lever moved the view: the summarizer returned a boundary that did not
        # advance (or an empty span), and nothing needed trimming. Rare, but it is a
        # real outcome and the transaction should say so rather than dangle.
        self._log_compaction_end("no_change", summarizer_tokens=summarizer_tokens)
        return None

    def _adopt_compaction(
        self,
        state: compaction.CompactionState,
        reason: str,
        text: str,
        *,
        measured: Optional[tuple[int, int]] = None,
        summarizer_tokens: int = 0,
        manual: bool = False,
        arguments_folded: int = 0,
    ) -> compaction.CompactionNotice:
        """Measure what a compaction actually bought, record the attempt, adopt the state.

        The measurement is the point. `_assert_smaller` inside `build_state` only refuses a
        compaction that GROWS the view, so an attempt that shrank it by one token passed as
        a success — and a policy that cannot tell "saved half the window" from "saved
        nothing" cannot decide to stop trying. Both ends are priced with the same estimator
        so the delta is the estimator's own unit rather than a mix of units.

        `measured` lets a caller that has already priced both ends pass them in; the prune
        path does, since it needs them to decide whether to adopt at all.
        """
        prior = self.compaction_state
        # A cut that replaces turns with a summary or a note carries what the harness itself saw
        # (`loop/witnessed.py`); a prune replaces nothing it would need to correct.
        if state.boundary_index > 0:
            from .witnessed import witnessed_record

            state.harness_record = witnessed_record(self.messages)
        # Image offload rides every adoption: images are
        # what makes a tail expensive, the decision is monotonic, and it changes the view —
        # so it is priced into the savings and named in the log like any other reduction.
        prior_offloaded = set(prior.offloaded_images if prior is not None else ())
        state.offloaded_images = compaction.offload_images(
            self.messages, prior=prior_offloaded
        )
        before, after = measured or (
            self._view_tokens(prior),
            self._view_tokens(state),
        )
        state.last_attempt_at = time.time()
        state.attempts = (prior.attempts if prior is not None else 0) + 1
        state.reason = reason
        state.saved_tokens = max(0, before - after)
        state.saved_pct = (state.saved_tokens / before) if before else 0.0
        prior_streak = prior.ineffective_streak if prior is not None else 0
        if manual:
            # A person's `/compact` neither feeds the automatic streak nor arms its
            # cooldown: mashing it twice on a quiet session must not pause automatic
            # compaction for `COMPACTION_COOLDOWN_SECONDS`. The prior streak carries
            # unchanged — a manual success is not evidence the automatic policy would
            # have succeeded either.
            state.ineffective_streak = prior_streak
        else:
            state.ineffective_streak = (
                prior_streak + 1
                if state.saved_pct < compaction.MIN_SAVINGS_PCT
                else 0
            )
        # A compacted BLOCK in the outbound view is a durable fact, and the one the
        # model-visibility invariant audits for (invariants.py): a request carrying
        # `<compacted-history>` must be explainable from the log. A prune-only state
        # (`boundary_index == 0`) puts no block in the view and logs only its attempt's
        # end below. The numbers ride here for the same reason they ride `turn/end`:
        # "why did context shrink, and by how much" should be answerable from the log.
        if state.boundary_index > 0:
            self.log.append(
                "compaction/summary",
                turn=self._turn_no,
                step=self._step_no,
                reason=reason,
                boundary=state.boundary_index,
                model=state.model_used or self.model,
                before_tokens=before,
                after_tokens=after,
                saved_tokens=state.saved_tokens,
                saved_pct=round(state.saved_pct, 4),
                summary_chars=len(state.summary_text),
                # The block's parts: how many, the most merges any one went
                # through, the merges this cut made, and the parts' size against their budget.
                parts=len(state.parts) or None,
                rewrites=compaction.max_rewrites(state.parts) if state.parts else None,
                merged=state.merged or None,
                parts_tokens=compaction.parts_tokens(state.parts) if state.parts else None,
                parts_budget=compaction.parts_budget(self._history_trigger_tokens()) if state.parts else None,
                attempts=state.attempts,
                summarizer_tokens=summarizer_tokens or None,
                images_offloaded=len(state.offloaded_images) or None,
            )
        self._log_compaction_end(
            reason,
            saved_tokens=state.saved_tokens,
            summarizer_tokens=summarizer_tokens,
            images_offloaded=len(state.offloaded_images),
            arguments_folded=arguments_folded,
        )
        self.compaction_state = state
        self._forget_context_measure()  # stale once the outbound view shrank
        if state.ineffective_streak >= compaction.INEFFECTIVE_STREAK_LIMIT:
            logger.warning(
                "compaction has saved under %.0f%% of the window %d times in a row "
                "(last: %d tokens); pausing it for %.0fs",
                compaction.MIN_SAVINGS_PCT * 100,
                state.ineffective_streak,
                state.saved_tokens,
                compaction.COMPACTION_COOLDOWN_SECONDS,
            )
        return compaction.CompactionNotice(
            text=text,
            reason=reason,
            saved_tokens=state.saved_tokens,
            saved_pct=state.saved_pct,
            now_tokens=after,
            # What the summarizer round-trip cost, when one ran. The counter the token
            # budget reads already includes it; this is the reader's copy.
            summarizer_tokens=summarizer_tokens,
            # The model's own summary, so the person can inspect what survives —
            # display-only, never re-entering the model's view.
            summary=state.summary_text or "",
        )

    def _log_compaction_end(
        self,
        reason: str,
        *,
        saved_tokens: int = 0,
        summarizer_tokens: int = 0,
        images_offloaded: int = 0,
        error: str = "",
        arguments_folded: int = 0,
    ) -> None:
        """Close one `compaction/start` transaction. One writer, so every exit from
        `_compact_now` (and every adoption) leaves the pair balanced — the property
        `SessionLog.dangling_compactions` reads. `error` carries the summarizer's
        exception text on the `failed` exit, so the durable record can tell a provider
        outage from a harness bug instead of the bare reason."""
        self.log.append(
            "compaction/end",
            turn=self._turn_no,
            step=self._step_no,
            reason=reason,
            saved_tokens=saved_tokens or None,
            summarizer_tokens=summarizer_tokens or None,
            images_offloaded=images_offloaded or None,
            arguments_folded=arguments_folded or None,
            error=error or None,
        )

    def _compaction_gate_now(self) -> tuple[bool, str]:
        """`compaction_gate` plus the engine-owned failure clock.

        The state-based gate only knows attempts that produced a state. A failed FIRST
        attempt (no state yet) left no stamp there, so `compaction_gate` answered "ok" and
        the next iteration paid for the same failure again — two summarizer calls per step,
        until the turn's cap. The failure path sets the clock and this reads it.
        """
        allowed, why = compaction.compaction_gate(
            self.compaction_state, now=time.time()
        )
        if not allowed:
            return allowed, why
        failed_at = self._compaction_failed_at
        if failed_at:
            since = time.time() - failed_at
            if since < compaction.COMPACTION_COOLDOWN_SECONDS:
                return False, f"the last attempt failed {since:.0f}s ago"
        return True, ""

    def _stamp_compaction_failure(self) -> None:
        # The engine clock first, so a failure with no state yet still backs off.
        self._compaction_failed_at = time.time()
        state = self.compaction_state
        if state is None:
            return
        state.last_attempt_at = time.time()
        state.attempts += 1
        state.reason = "failed"

    def _standing_plan(self) -> Optional[list[dict[str, str]]]:
        """The task list as it stands RIGHT NOW, for the compacted block.

        Live, not mined from the summarized span: a plan written before the boundary and
        updated after it would put a stale list in a block sitting in front of the
        verbatim tail that contradicts it. This copy is true whichever side of the
        boundary the last write landed on.
        """
        return None if self.todo is None else list(self.todo.items)

    def _log_call(self, tool_call: ToolCall, *, parallel: bool = False) -> None:
        """Record the call at the moment it starts. Arguments go in as the tool received
        them: the log answering "why did it call that" needs what was SENT, and a
        re-serialised copy is a different string from the one the tool received. A name taken
        from another harness is under `renamed`, as it was sent."""
        renamed = getattr(self, "_renamed_arguments", {}).pop(tool_call.id, None)
        self.log.append(
            "tool/call",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            arguments=log_text(tool_call.arguments),
            parallel=parallel or None,
            renamed=log_text(renamed) if renamed else None,
        )

    def _log_tool_result(self, tool_call: ToolCall, result: Any, status: str) -> None:
        """`tool/result` for the paths that bypass `_record_result` — the approver and
        denial flows append their result messages directly. Same event shape, minus the
        timing and contract rendering those paths never had."""
        self.log.append(
            "tool/result",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            content=log_text(result),
            is_error=status != "ok",
            status=status,
        )

    def _log_partial_assistant(
        self, streamed: list[str], streamed_reasoning: list[str]
    ) -> None:
        """A partial the user watched arrive enters history on the failure and stop
        paths; the log records it like any other assistant message, `partial` marking
        that it carries no usage and no completed timing."""
        self.log.append(
            "assistant/message",
            turn=self._turn_no,
            step=self._step_no,
            model=self.model,
            text="".join(streamed),
            reasoning="".join(streamed_reasoning) or None,
            partial=True,
        )

    def _log_turn_end(
        self, reason: TurnEndReason, *, synthetic: bool = False, error: str = "", error_at: str = ""
    ) -> None:
        """The one writer of `turn/end`. Every way a turn ends goes through here, so
        the reason vocabulary (`turns.TurnEndReason`) has a single enforcement point
        and the loop finalizer can tell a closed turn from an open one."""
        self._turn_end_logged = True
        self.log.append(
            "turn/end",
            turn=self._turn_no,
            reason=reason.value,
            steps=self._step_no,
            # What the turn cost, in tokens — the same counter the token budget measures
            # (`TokenUsage.total`, delegated work folded in). It rides every ending
            # because "what did this turn cost" is a question the log should answer
            # without the reader reconstructing it from every assistant/message above.
            tokens=self._turn_tokens,
            # Why an `error` ending happened, as a class (`providers.errors.turn_error_class`,
            # `hook_denied`, `crashed`): the notice's text is for the person, this is for counting.
            error=error or None,
            # Where this app's own code raised, for an ending that is its fault
            # (`turn_errors.raised_at`: exception type and code location, never its text).
            error_at=error_at or None,
            **({"synthetic": True} if synthetic else {}),
        )

    def _force_close_turn(self, reason: TurnEndReason, *, error: str = "", error_at: str = "") -> None:
        """Close a turn the normal paths never reached — an exception, a cancellation,
        or the consumer walking away mid-turn. Idempotent via `_log_turn_end`."""
        if self._turn_end_logged:
            return
        self._log_turn_end(reason, error=error, error_at=error_at)

    def _close_dangling_turn(self) -> None:
        """Crash recovery (every turn is closed): a process death leaves the
        log holding `turn/start` with no `turn/end`. Close each such turn
        synthetically — reason `interrupted`, marked `synthetic` so no reader mistakes
        it for something the engine said — preserving the turn's real events and
        repairing only the boundary. Runs once per attached log, on the only path that
        can see the shape: a fresh engine (no turn of its own open) adopting a log a
        dead process wrote.

        The same repair covers compaction: a process killed while the summarizer call
        was in flight leaves `compaction/start` with no `compaction/end`. Nothing is
        corrupted (the canonical history was never touched), but an open transaction
        reads as still running, so each one is closed with reason `interrupted`.
        """
        if self._turn_no != 0 or self._dangling_checked_for is self.log:
            return
        self._dangling_checked_for = self.log
        if not self.log.enabled:
            return
        for turn in self.log.dangling_turns():
            self.log.append(
                "turn/end",
                turn=turn,
                reason=TurnEndReason.INTERRUPTED.value,
                synthetic=True,
            )
        for _ in range(self.log.dangling_compactions()):
            self.log.append(
                "compaction/end", reason="interrupted", synthetic=True
            )
        # Continue the log's numbering instead of starting again at 1: a turn
        # number names one turn for the life of the log, whichever engine ran it.
        self._turn_no = last_turn(self.log.read())

    def _note_tool_changes(self) -> None:
        """Record the request header at every point this session's toolset changed.

        On CHANGE only: the header is the same bytes every other turn, and a copy per turn
        would be the largest thing in the log by an order of magnitude while saying nothing
        new. The first pass counts as a change — that entry is the session's opening set.

        This also used to stamp a `tools_changed` notice into the transcript. Dropped on the
        owner's call (2026-09-10): `itemsFromMessages` never had a branch for that kind, so
        it fell through to the catch-all error branch and a mid-session MCP connect was
        replayed as `Error: Tools available to the agent changed: +mcp__…`, retriable, on a
        session where nothing had gone wrong. Nothing was lost with it — the notice was
        display-only (`_outbound_messages` drops every `notice` message, so no model ever
        read one), and this header records the same change with the full schemas rather
        than a list of names.
        """
        # A name registered or dropped can change the wire (a schema, or `tool_search`'s index),
        # and loading a deferred tool no longer does: its schema reaches the model in
        # the describe result, which the log already holds.
        current = self._tool_list_now()
        previous = self._tool_signature
        self._tool_signature = current
        # The system prompt is part of the header too: a model switch rewrites its model line
        # (`context/model_line.py`) with the tools unchanged, and a prompt the model reads must reach
        # the log.
        system = next((m.get("content") for m in self.messages if m.get("role") == "system"), None)
        said, self._system_logged = self._system_logged, system
        if previous == current and system == said:
            return
        self.log.append(
            "request/header",
            turn=self._turn_no,
            step=self._step_no,
            reason="initial" if previous is None else "tools" if previous != current else "system",
            model=self.model,
            system=system,
            tools=self.registry.schemas(),
        )

    def _note_revert_change(self) -> None:
        """Record the undo marker the next request is built under, when it changed.

        `/undo` and `/redo` change what reaches the provider without adding a
        message, and wrote nothing here: a request sent after an undo could not be rebuilt
        from its log, and the cache drop the new prefix causes read as unexplained (ADR
        0129). A marker can be staged with no engine alive (a session opened and undone
        before its first turn), so the row is written where the marker first shapes a
        request, not where it is set; a new engine starts from what the log last said.
        """
        current = _revert.marker_key(self.revert_state)
        if self._revert_logged is False:
            self._revert_logged = _revert.last_logged(self.log.read() if self.log.enabled else ())
        if current == self._revert_logged:
            return
        self._revert_logged = current
        if current is None:
            self.log.append("revert/set", turn=self._turn_no, step=self._step_no, cleared=True)
        else:
            self.log.append(
                "revert/set", turn=self._turn_no, step=self._step_no, boundary=current[0], staged_at=current[1]
            )

    def _tool_list_now(self) -> frozenset[str]:
        """What decides the tool list on the wire: the names registered. `_tool_signature` is
        this, as of the last request."""
        return frozenset(self.registry.names())

    def _prune_candidate(
        self, trigger: int
    ) -> Optional[tuple[compaction.CompactionState, int, int, int, int]]:
        """The cheap lever's arithmetic, shared by the pressure path (`_prune_now`) and
        the cache-cold path (`_opportunistic_prune`): `(candidate, upto, before, after,
        folded)`, or None when nothing aged is worth rewriting.

        Synchronous and model-free on purpose: this is the branch that exists so a long
        session does not have to pay a summarizer round-trip for output it will never
        look at again.
        """
        state = self.compaction_state
        old_upto = state.pruned_upto if state is not None else 0
        upto = compaction.prune_boundary(
            self.messages,
            keep_tokens=compaction.prune_keep_tokens(trigger),
        )
        if upto <= old_upto:
            return None  # nothing has aged since the last pass
        if state is None:
            candidate = compaction.CompactionState(
                boundary_index=0, summary_text="", working_state="", pruned_upto=upto
            )
        else:
            candidate = replace(state, pruned_upto=upto)
        # Forward-only: a state that folded arguments keeps folding them, and one that dropped
        # re-runnable output keeps dropping it.
        candidate.prune_arguments = candidate.prune_arguments or self._prunes_arguments()
        candidate.prune_rederivable = candidate.prune_rederivable or self._prunes_rederivable()
        # The prune tier carries the image offload too: it is the free pass, and images are
        # the other kind of payload that never ages out on its own.
        candidate.offloaded_images = compaction.offload_images(
            self.messages,
            prior=(state.offloaded_images if state is not None else ()),
        )

        before = self._view_tokens(state)
        after = self._view_tokens(candidate)
        if after >= before:
            return None  # nothing aged was large enough to be worth rewriting
        folded = compaction.count_fold_results(self.messages, old_upto, upto)
        return candidate, upto, before, after, folded

    def _prune_now(
        self, trigger: int
    ) -> Optional[compaction.CompactionNotice]:
        """Shorten aged tool results in the outbound view. Returns the notice when that
        alone got the window under `trigger`, else None — and then leaves the advanced
        prune point in place regardless, because the summarizer that runs next has less
        to read and the reduction is real either way.
        """
        result = self._prune_candidate(trigger)
        if result is None:
            return None
        candidate, upto, before, after, folded = result
        old_upto = self.compaction_state.pruned_upto if self.compaction_state is not None else 0
        arguments = self._arguments_folded(candidate, old_upto, upto)
        if after >= trigger:
            # Real, but not enough — the caller goes on to summarize. The state is advanced
            # (the reduction is real either way) but NOT stamped: the attempt worth
            # recording is the one that finished the job, and leaving the record to the
            # summarizer keeps `reason` describing the lever that resolved the pressure
            # rather than the one that only helped.
            self.compaction_state = candidate
            self._forget_context_measure()
            return None
        if folded:
            text = (
                "Older tool output was shortened to free up context — "
                f"{folded} large output{'s' if folded != 1 else ''} folded to files, "
                "readable by path"
            )
        else:
            text = "Older tool output was shortened to free up context"
        return self._adopt_compaction(
            candidate, "pruned", text, measured=(before, after), arguments_folded=arguments
        )

    def _prunes_arguments(self) -> bool:
        """Whether this engine's prune folds aged write payloads too: on
        unless the engine or `[compaction] prune_write_arguments` says false."""
        override = getattr(self, "prune_write_arguments", None)
        if override is not None:
            return bool(override)
        return bool(self._compaction_config().get("prune_write_arguments", True))

    def _arguments_folded(
        self, candidate: compaction.CompactionState, old_upto: int, upto: int
    ) -> int:
        """How many calls a pass from `old_upto` to `upto` folds, for the log."""
        if not candidate.prune_arguments:
            return 0
        return compaction.count_folded_arguments(self.messages, old_upto, upto)

    def mark_prefix_dirty(self) -> None:
        """The outbound prefix changed outside compaction — a staged revert hides a span
        of canonical history — so the provider's cached prefix is unusable for the next
        request and the opportunistic prune may run for free. Called by the manager's
        revert path (`server/manager/revert.py`)."""
        self._prefix_dirty = True

    def _cache_ttl_seconds(self) -> Optional[float]:
        """How long the provider's prompt-cache prefix survives between requests.

        `[compaction] prune_after_idle_seconds` overrides it (a number forces that idle
        window; 0 disables the opportunistic pass outright). With no override the
        provider answers for itself — only a provider that MANAGES its cache TTL
        (Anthropic's 5m/1h breakpoints) exposes `cache_ttl_seconds`; an unknown TTL
        answers None, and nothing is pruned on a guess."""
        override = self._compaction_config().get("prune_after_idle_seconds")
        if override is not None:
            try:
                seconds = float(override)
            except (TypeError, ValueError):
                seconds = 0.0
            return seconds if seconds > 0 else None
        getter = getattr(self.provider, "cache_ttl_seconds", None)
        if not callable(getter):
            return None
        try:
            return getter(self.model)
        except Exception:  # noqa: BLE001 — a provider that cannot answer means "unknown"
            return None

    def _cache_cold(self) -> bool:
        """Whether the provider's cached prefix is (or is about to be) unusable, so
        folding aged results now costs no cache hit.

        Four proofs, no guesses: a dirty prefix (a staged revert changed the view); a
        model other than the one the last request went to (a cache belongs to one model);
        a tool list other than the one the last request carried (it leads the prefix, so
        nothing after it can match); or idle past the provider's cache TTL. An engine
        that never called the provider, or one whose TTL is unknown, answers False — the
        conservative reading, because a wrong "cold" buys a full-price request.

        Measured 2026-10-06: a model switch (19 of them, every one reading nothing from
        the cache) and a changed tool list were where most full-price requests came
        from, and neither was a proof here. A TTL is still not guessed for a provider
        that declares none: across the store the cache survived a one-to-four hour gap
        on one route (11 of 11) and not on another (0 of 2).
        """
        if self._prefix_dirty:
            return True
        last = self._last_request_at
        if last is None:
            return False
        if self._last_request_model is not None and self._last_request_model != self.model:
            return True
        if self._tool_signature is not None and self._tool_signature != self._tool_list_now():
            return True
        ttl = self._cache_ttl_seconds()
        if ttl is None:
            return False
        return (time.time() - last) >= ttl

    def _opportunistic_prune(self) -> bool:
        """Fold aged tool results while the prefix cache is known cold — the
        "microcompaction" tier, without the API's help.

        Pruning rewrites the middle of the prompt, so a provider matching a cached prefix
        byte for byte re-prices everything after the first rewritten result. That is why
        the pressure path prunes only when compaction is due and the cost is already
        paid. When the cache is ALREADY unusable (idle past its TTL, or a staged revert
        changed the view), the rewrite is free: the next request was going to miss
        anyway, and it now misses on a smaller prompt. Silent — the model sees the stubs
        on its next request, while the durable `compaction/prune` event carries the
        numbers for whoever audits the session."""
        if not self._cache_cold():
            return False
        self._prefix_dirty = False  # the decision is spent whether or not anything aged
        result = self._prune_candidate(self._history_trigger_tokens())
        if result is None:
            return False
        candidate, upto, before, after, folded = result
        old_upto = self.compaction_state.pruned_upto if self.compaction_state is not None else 0
        arguments = self._arguments_folded(candidate, old_upto, upto)
        self.compaction_state = candidate
        self._forget_context_measure()
        self.log.append(
            "compaction/prune",
            turn=self._turn_no,
            step=self._step_no,
            reason="cache_cold",
            before_tokens=before,
            after_tokens=after,
            saved_tokens=before - after,
            pruned_upto=upto,
            folded=folded or None,
            arguments_folded=arguments or None,
            images_offloaded=len(candidate.offloaded_images) or None,
        )
        return True

    def _prune_spill(self) -> Optional[Any]:
        """A content-addressed spill sink for the prune tier, or None without a store.

        `prune_tool_results` runs on EVERY outbound render; a naive sink would write a new
        file per render for the same result. This one keys on the text: the first render
        writes once, every later render reuses the same path — so the view stays
        byte-stable (which the provider cache also reads as "unchanged") and the model
        gets a `read_file` path for a result that had no locator.
        """
        store = getattr(getattr(self, "world", None), "spill", None)
        save = getattr(store, "save", None)
        if not callable(save):
            return None

        def sink(text: str) -> Optional[str]:
            digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
            if digest in self._spill_paths:
                return self._spill_paths[digest] or None
            path = save(text, label="pruned") or ""
            if len(self._spill_paths) >= _SPILL_CACHE_MAX:
                self._spill_paths.clear()
            self._spill_paths[digest] = path
            return path or None

        return sink

    def _history_trigger_tokens(self) -> int:
        """The current model's compaction trigger, from live settings. One place, so the
        due-check, the compact path, and the restore fit test cannot drift apart."""
        cfg = self._compaction_config()
        output = int(cfg.get("output_tokens") or 0)
        buffer = int(cfg.get("buffer_tokens") or 0)
        uncapped = cfg.get("uncapped_window")
        if uncapped:
            # A cap bounds the PROMPT; the answer's reserve protects the REAL window. Taking
            # the reserve out of the capped window instead halved the trigger for any model
            # whose declared output passes half the cap: deepseek-v4.1-flash (384k output)
            # compacted at 200k under the 400k ceiling, and its 0.7 prepare (removed, ADR
            # 0077) fired at 140k — 9 pre-step stalls of 30-93s in one session (2026-09-24, first after-change
            # afternoon), 17% of its tokens in summarizer replays.
            capped = int(cfg.get("context_window") or 0)
            real = compaction.trigger_tokens(
                uncapped, output_tokens=output, buffer_tokens=buffer
            )
            return max(1, min(capped - buffer, real))
        return compaction.trigger_tokens(
            cfg.get("context_window"), output_tokens=output, buffer_tokens=buffer
        )

    async def compact_now(self) -> dict[str, Any]:
        """Condense this session's history on demand — the human `/compact`.

        Unlike the automatic path this runs BELOW pressure (a person asked, and that is
        the whole point) and skips the prune tier (the request is for the summary, not
        for aged outputs to be shortened). The result carries the numbers a surface
        reports, or a reason nothing could be done; on success the projection is live in
        `compaction_state` and the caller persists the engine.

        Refused while the session is stopping. Called by the manager only at idle (it
        holds the session's running mark across the summarizer call), so no turn can be
        building the outbound view underneath this.
        """
        if self._cancel.is_set():
            return {"ok": False, "error": "the session is stopping"}
        self._last_compaction_error = ""
        notice = await self._compact_now(manual=True)
        if notice is None:
            if self._last_compaction_error:
                return {
                    "ok": False,
                    "error": "the summarizer could not condense this history",
                    # The reason, so the surface and the person can tell an outage from a
                    # harness bug (study round 28/29).
                    "detail": self._last_compaction_error[:300],
                }
            return {
                "ok": False,
                "error": "nothing to compact — no older span can be summarized",
                "reason": "nothing",
            }
        # The persisted marker, exactly as the automatic path writes it. Without it a
        # manual compaction left no trace in the transcript, and a reload would show a
        # smaller context with nothing saying why.
        self._append_notice("compacted", notice.text, **notice.numbers)
        return {
            "ok": True,
            "text": notice.text,
            "reason": notice.reason,
            "saved_tokens": notice.saved_tokens,
            "saved_pct": round(notice.saved_pct, 4),
            "now_tokens": notice.now_tokens,
            "summarizer_tokens": notice.summarizer_tokens,
            # The model's own summary, so a surface that shows the marker live carries the
            # same disclosure a reload would (the notice knows it; the caller used to not).
            "summary": notice.summary,
        }

