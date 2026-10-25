"""ContextFoldMixin — the model folds its own context.

Context Language Models (arXiv 2609.37725): leave the mechanism of context management with the
harness and give its POLICY to the model. Here the mechanism is the compaction transaction this
engine already has: `compaction/start`, a `compaction/summary` for the block, an
`end`, the state adopted through `_adopt_compaction`, the canonical history untouched. A fold
is that transaction with two things moved to the model — when it runs (a tool call) and who
writes the block's summary (the call's `note`). It is not a second path: the threshold
compaction stays underneath as the floor, and a session whose model never folds behaves
exactly as it did.

On by default for a session's own engine (`[compaction] model_fold = false` turns it off;
a subagent is not offered it). What it is worth was measured (`benchmarks/ablation.py`): a
third fewer tokens on two models, and a bill that depends on one thing the harness can see
for itself — whether the provider's cache survives a fold. So it looks (`_fold_cache_probe`):
on a route where the request after a fold reads nothing from a cache that was warm, the
hints stop for that model.
"""
from __future__ import annotations

import time
from harness.context import compaction
from harness.session.turns import ATTACHED_BY
from typing import Any, Optional

from harness.session import revert as _revert
from harness.tools.context_fold import FOLD_CONTEXT

#: A hint needs this much FOLDABLE context: this many tokens, or this share of the compaction
#: trigger when that is lower. Measured as the span a fold would replace and not as the whole
#: context: about 18k tokens of every request are the system prompt and the tool schemas,
#: which no fold removes, so a rule on the total fired on the second step of a small-window
#: session with nothing yet to fold (first pilot, 2026-10-04: two hints in four steps).
#: 12,000 is the measured figure, not a guess: it is what the long-session ablation ran with
#: (deepseek-v4.1-flash, 3 runs each way: 37.6% fewer tokens, the same pass rate). The first
#: guess, 40,000, never acted on that suite at all.
FOLD_HINT_FRACTION = 0.25
FOLD_HINT_TOKENS = 12_000
#: …and a fold must at least HALVE the request to be asked for. A fold removes
#: the foldable span and nothing else: the system prompt, the tool schemas, the notes and
#: the kept tail all stay, about 30k in a real session. Asked for at 15k foldable of 46k,
#: a model spent a 47k request and 13 s to make each later step 13k smaller, which needs
#: four more steps to break even in a store whose median turn HAS four (2026-10-05). The
#: Anthropic API's context editing has `clear_at_least` for the same reason: rewriting a
#: prompt is only worth it when enough is cleared.
FOLD_HINT_SHARE = 0.5
#: With no boundary in sight, the hint is given anyway once this many times the threshold
#: could be folded: a turn that never finishes an item must not be left to the trigger alone.
FOLD_HINT_LATE_FACTOR = 3
#: WHEN it is given matters more than how much there is (second pilot, 2026-10-04: one hint
#: by size, landing between "read twenty files" and "edit them"; the model ignored it, rightly,
#: and by the time that piece of work was done the hint was spent — zero folds in 27 steps).
#: So it is given where work has just ended: a new message from the person, or a task-list
#: item marked done.
#: At a boundary the hint ASKS for the fold. A statement of the facts ("…fold_context replaces
#: them with a note you write") was given at five boundaries across three pilot runs on
#: deepseek-v4.1-flash and drew no fold and no mention of folding in the reasoning; CLM's own
#: way to get a behaviour is a sentence that asks for it. The model still decides what is
#: kept, and may decline when it needs the details.
#: The fold rides a WORKING step. Asked to fold "first … then begin", a model gave the fold a
#: step of its own five times out of five: a request at the full size and 13-17 s in which
#: nothing the person asked for moved. AgentFold decides the fold in the same response as
#: the action, and so does this wording.
FOLD_HINT_TEXT = (
    "System note: you just finished an item and more are still to start; ~{foldable} of the "
    "~{used} tokens in your context are older than your recent steps. Call fold_context in the "
    "SAME step as the calls that start the next item (it needs no step of its own, and what "
    "those calls return is kept), with a note of what you still need from the older part: "
    "decisions, exact names and paths, what is verified, what is left. Skip it only if you "
    "still need those details word for word."
)
FOLD_HINT_LATE_TEXT = (
    "System note: your context holds ~{used} tokens, ~{foldable} of them older than your "
    "recent steps; at ~{trigger} the harness summarizes it for you. When the piece of work "
    "you are on is finished, fold_context replaces the older part with a note you write, so "
    "you choose what is kept."
)
FOLD_HINT_TURN_TEXT = (
    "~{foldable} of the ~{used} tokens in your context are from before this message. If this "
    "message starts a new piece of work, call fold_context in the SAME step as your first "
    "calls for it (it needs no step of its own, and what those calls return is kept), with a "
    "note of what you still need from the earlier work."
)
#: The verbatim tail a fold keeps. NOT the compaction's `keep_tokens`: that one is sized for
#: a harness that cuts without knowing where the work stands (under a capped window it is a
#: quarter of the window, up to 40k, because a short tail made a model redo work). A fold
#: happens when the model says a piece of work is finished, so its last few steps are
#: enough — and a 40k tail would keep exactly the steps it just declared done (third pilot,
#: 2026-10-04: under the product's cap a fold could free nothing until 60k had piled up).
FOLD_KEEP_TOKENS = compaction.DEFAULT_KEEP_TOKENS
#: What a fold must free, as a share of the view, to be applied. Below it the model would pay
#: a rewritten prompt for nothing; the refusal tells it to wait.
FOLD_MIN_SAVINGS_PCT = compaction.MIN_SAVINGS_PCT


def _k(value: int) -> str:
    return f"{value / 1000:.0f}k" if value >= 1_000 else str(max(0, int(value)))


class ContextFoldMixin:
    def disable_model_fold(self) -> None:
        """Take `fold_context` away again (the config switch, and the ablation's)."""
        self.model_fold = False
        self.registry.remove(FOLD_CONTEXT)

    def _fold_cache_probe(self, usage: Any) -> None:
        """Called with every request's usage: did the cache survive the fold before it?

        A fold rewrites the prompt from the block on. The system prompt and the tool schemas
        stand BEFORE the block and are unchanged, so a provider that caches by prefix still
        serves them: on deepseek-v4.1-flash the request after a fold read 15.8k tokens from
        cache and paid for 2.8k (0 misses in 9). On mimo-v2.5 it read nothing in 9 of 14 and
        paid for 16k, which doubled the session's input priced in full (Measured).
        The route cannot be known in advance, so it is asked once and believed, the way the
        summarizer replay is (`_summarizer_replay_pays`): a cache that was WARM on the request
        that folded and empty on the next one means folding costs this model a full prompt
        each time, and the hints stop for it. A cache that was already cold says nothing.
        """
        read = int(getattr(usage, "cache_read", 0) or 0)
        warm_before, self._fold_probe = self._fold_probe, None
        self._fold_last_cache_read = read
        if not warm_before or read > 0 or self.model in self._fold_cache_lost:
            return
        self._fold_cache_lost.add(self.model)
        self.log.append(
            "context/budget",
            turn=self._turn_no,
            step=self._step_no,
            used=int(getattr(usage, "context_tokens", 0) or 0),
            trigger=self._history_trigger_tokens(),
            hint="fold_off",
            model=self.model,
        )

    def enable_model_fold(self) -> None:
        """Offer `fold_context` in this session. Session-bound: the tool acts on
        the context of the engine that holds it, so a child's copied registry never gets it."""
        from harness.tools.context_fold import context_fold_tools

        self.model_fold = True
        if self.registry.get(FOLD_CONTEXT) is None:
            self.registry.register_all(context_fold_tools(self.fold_context), session_bound=True)

    def fold_context(self, note: str) -> dict[str, Any]:
        """Apply the model's fold, or say why not. Never raises: this is a tool's answer.

        Synchronous, and model-free while the block's parts fit their budget: the note was
        written in the step that asked for the fold, on the cached prefix. Past the budget two
        earlier parts are combined in one small call on their text alone, where the
        model used to be asked for a note replacing all of them.
        """
        if not getattr(self, "model_fold", False):
            return {"error": "folding is not enabled in this session"}
        if _revert.marker_boundary(self.revert_state) is not None:
            # A staged undo hides a span of the canonical list; a block built now would
            # summarize messages the person has just taken back.
            return {"error": "an undo is staged; fold after it is kept or redone"}
        trigger = self._history_trigger_tokens()
        keep = self._fold_keep_tokens()
        prior = self.compaction_state
        try:
            state = compaction.build_fold_state(
                self.messages,
                note=note,
                keep_tokens=keep,
                prior=prior,
                trigger=trigger,
                standing_plan=self._standing_plan(),
                approved_plan=self.approved_plan,
                plan_path=str(self.plan_path) if self.plan_path is not None else "",
                model=self.model,
            )
        except compaction.FoldRefused as refused:
            return {"error": str(refused)}
        before = self._view_tokens(prior)
        after = self._view_tokens(state)
        if before <= 0 or (before - after) < FOLD_MIN_SAVINGS_PCT * before:
            return {
                "error": (
                    f"folding now would free ~{max(0, before - after):,} of ~{before:,} "
                    "tokens. Keep working and fold when more has accumulated."
                )
            }
        # Past the parts' budget two earlier parts are combined on their text alone, by the
        # compaction's model. Only for a fold that will be applied: a merge is a model
        # call, and it only makes the view smaller, so the decision above holds after it.
        spent: list[int] = []
        cfg = (self.compaction_settings() or {}) if self.compaction_settings else {}
        parts, merged = compaction.settle_parts(
            state.parts, provider=self.provider, model=str(cfg.get("model") or "") or self.model,
            summary_tokens=compaction.summary_target(trigger),
            output_limit=int(cfg.get("output_tokens") or 0) or None,
            usage_sink=lambda usage: spent.append(int(getattr(usage, "total", 0) or 0)),
        )
        if merged:
            state.parts, state.merged, state.summary_text = parts, merged, compaction.render_parts(parts)
            after = self._view_tokens(state)
        summarizer_tokens = sum(spent)
        self._turn_tokens += summarizer_tokens
        self.log.append(
            "compaction/start",
            turn=self._turn_no,
            step=self._step_no,
            trigger=trigger,
            keep_tokens=keep,
            model=self.model,
            before_tokens=before,
            signal=self._context_signal(),
            author="model",
            unsettled=self._work_unsettled() or None,
        )
        folded = state.boundary_index - (prior.boundary_index if prior is not None else 0)
        kept = len(self.messages) - state.boundary_index
        # `manual=True`: like a person's `/compact`, a fold is not the automatic policy
        # trying, so it neither feeds that policy's ineffective streak nor arms its cooldown.
        notice = self._adopt_compaction(
            state,
            "folded",
            "Context folded — the model replaced earlier turns with its own note",
            measured=(before, after),
            manual=True,
            summarizer_tokens=summarizer_tokens,
        )
        self._fold_notice = notice
        # The next request answers whether this route's cache survives a fold.
        self._fold_probe = self._fold_last_cache_read > 0
        return {
            "folded_messages": folded,
            "kept_messages": kept,
            "before_tokens": before,
            "after_tokens": after,
            "saved_tokens": notice.saved_tokens,
        }

    def _take_fold_notice(self) -> Optional[compaction.CompactionNotice]:
        """The fold a tool call just applied, once — for the loop to put in the transcript
        and on the event stream, the way it does for a compaction it ran itself."""
        notice, self._fold_notice = getattr(self, "_fold_notice", None), None
        return notice

    def _fold_keep_tokens(self) -> int:
        configured = (self.compaction_settings() or {}).get("fold_keep_tokens") if self.compaction_settings else None
        return int(configured) if configured else FOLD_KEEP_TOKENS

    def _fold_hint_tokens(self) -> int:
        """How much foldable context it takes for the hint to be given. `[compaction]
        fold_hint_tokens` replaces the token figure (the share of the trigger still caps it)."""
        trigger = self._history_trigger_tokens()
        configured = (self.compaction_settings() or {}).get("fold_hint_tokens") if self.compaction_settings else None
        tokens = int(configured) if configured else FOLD_HINT_TOKENS
        return max(1, min(int(FOLD_HINT_FRACTION * trigger), tokens))

    def _fold_repays(self, used: int, foldable: int) -> bool:
        """Whether a fold now would take at least `FOLD_HINT_SHARE` off the request
        (`[compaction] fold_hint_share`; 0 asks whenever the token threshold is met)."""
        configured = (self.compaction_settings() or {}).get("fold_hint_share") if self.compaction_settings else None
        share = FOLD_HINT_SHARE if configured is None else float(configured)
        return used <= 0 or foldable >= share * used

    def _foldable_tokens(self, used: int) -> int:
        """What a fold would replace now, in the provider's unit: everything since the last
        boundary, less the tail a fold keeps.

        `used` minus a floor. The floor is what a fold cannot remove — the system prompt, the
        tool schemas and any block already there — taken once per stretch as the provider's
        count minus the conversation since the boundary (one pass over it), so the check that
        runs after every step is a subtraction.
        """
        state = self.compaction_state
        boundary = state.boundary_index if state is not None else 0
        floor = self._fold_floor
        if floor is None or floor[0] != boundary:
            start = boundary or (1 if self.messages and self.messages[0].get("role") == "system" else 0)
            since = self._priced(
                compaction.estimate_tokens(self.messages[start:], echo=self._reasoning_echo())
            )
            floor = self._fold_floor = (boundary, max(0, used - since))
        return max(0, used - floor[1] - self._fold_keep_tokens())

    def _fold_hint_due(self, used: int, moment: str = "turn") -> Optional[int]:
        """The foldable tokens when a hint may be given at this `moment`, else None — and it
        is then taken. Each moment keeps its own book, because each pilot that shared one lost
        a hint to it (2026-10-04):

        turn   a new message. Due whenever a threshold can be folded: it comes once per turn
               by construction, and it is the moment the model actually folds at.
        item   a task-list item just marked done WITH ANOTHER NOT YET STARTED. May repeat
               in one stretch, but only after the stretch has grown by a whole threshold
               since the last one: a model that declined at 45k is not asked again at 46k.
        late   no boundary in sight and three thresholds piled up. Once per stretch.
        """
        if not getattr(self, "model_fold", False) or self.registry.get(FOLD_CONTEXT) is None:
            return None
        if self.model in self._fold_cache_lost:
            return None  # on this route a fold costs a full prompt: not asked for again
        state = self.compaction_state
        boundary = state.boundary_index if state is not None else 0
        foldable = self._foldable_tokens(used)  # always, so the stretch's floor is taken early
        step = self._fold_hint_tokens()
        if not self._fold_repays(used, foldable):
            return None
        if moment == "late":
            if self._fold_late_hinted == boundary or foldable < step * FOLD_HINT_LATE_FACTOR:
                return None
            self._fold_late_hinted = boundary
            return foldable
        if moment == "item":
            last = self._fold_hinted
            need = last[1] + step if last is not None and last[0] == boundary else step
            if foldable < need:
                return None
            self._fold_hinted = (boundary, foldable)
            return foldable
        return foldable if foldable >= step else None

    def _log_fold_hint(self, used: int, foldable: int, moment: str, **where: Any) -> dict[str, Any]:
        trigger = self._history_trigger_tokens()
        return {
            "used": used,
            "foldable": foldable,
            "trigger": trigger,
            "band": max(0, min(10, int(used / trigger * 10))) if trigger > 0 else 0,
            "hint": "fold",
            "moment": moment,
            **where,
        }

    def _stamp_fold_hint(self, message: dict[str, Any]) -> None:
        """At a turn's start: say how much of the context is from before this message.

        A new message from the person is the clearest boundary there is. It rides the same
        stamp as the `<context-budget>` line — the append-only moment — so no extra message
        enters the transcript; the turn logs it right after `user/message`.
        """
        used = self._context_signal()
        foldable = self._fold_hint_due(used)
        if foldable is None:
            return
        text = "\n\n<context-fold>\n" + FOLD_HINT_TURN_TEXT.format(
            used=_k(used), foldable=_k(foldable)
        ) + "\n</context-fold>"
        self._fold_stamp = self._log_fold_hint(used, foldable, "turn")
        base = message.get("api_content")
        content = message.get("content")
        if isinstance(content, str):
            message["api_content"] = (base if isinstance(base, str) else content) + text
        elif isinstance(content, list):  # content-parts (text + images)
            message["api_content"] = [
                *(base if isinstance(base, list) else content),
                {"type": "text", "text": text},
            ]

    def _items(self) -> tuple[int, int]:
        """`(done, not yet started)` on the task list."""
        items = getattr(getattr(self, "todo", None), "items", None) or []
        states = [item.get("status") for item in items if isinstance(item, dict)]
        return sum(1 for s in states if s == "done"), sum(1 for s in states if s == "pending")

    def _maybe_fold_hint(self) -> None:
        """After a step: a hint if an item was just finished and work remains, or (late) if
        a great deal has accumulated with no boundary at all. One system note, logged with
        its numbers.

        A fold is repaid by the steps that come after it: it costs a request at the full
        size and a note, and each later step is smaller by what it removed. When the item
        just finished leaves nothing to START, what comes after is the answer. Real session
        (14715fae-61f): asked after its last item, the model spent a step of 50k tokens
        and 13 s writing its test results into a note, then wrote them again as its
        report, from the note. "Another item still open" was not enough: the next day the
        one open item was the report itself, already in progress, and the same thing
        happened. An item in progress is work under way; only one not yet started is a
        piece of work a fold can be repaid by. The next message from the person is asked
        about at its own boundary (`turn`), so nothing is lost by not asking here."""
        if self._work_unsettled():
            return  # mid-edit by the completion gates' own facts: asked once it settles (0107)
        done, to_start = self._items()
        finished = done > self._fold_done_seen and to_start > 0
        self._fold_done_seen = done
        used = self._context_signal()
        foldable = self._fold_hint_due(used, "item" if finished else "late")
        if foldable is None:
            return
        template = FOLD_HINT_TEXT if finished else FOLD_HINT_LATE_TEXT
        row = self._log_fold_hint(
            used, foldable, "item" if finished else "late", turn=self._turn_no, step=self._step_no,
        )
        self.messages.append(
            {
                "role": "user",
                "content": template.format(
                    used=_k(used), foldable=_k(foldable), trigger=_k(row["trigger"])
                ),
                ATTACHED_BY: "fold_hint",
                "ts": time.time(),
            }
        )
        # Model-visible means logged (0001): the sentence is rebuilt from these numbers.
        self.log.append("context/budget", **row)
