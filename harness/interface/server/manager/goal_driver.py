"""GoalDriverMixin — the goal-round driver and its ledger stops.

Split out of `wake.py` (size ceiling). One serialized driver pass per session opens the
next round while the session is idle; after each round the ledger records what it
moved and may end the goal (proven, budget spent, or stalled). The human's controls live
in `goal_controls.py`; the turn itself is driven by `WakeMixin.deliver_to_session`.
"""
from __future__ import annotations

import logging

import asyncio

from harness.loop.goals import ACT_ARMED, ACT_DISARMED, GoalStore, PHASE_ACTIVE, PHASE_BLOCKED, PHASE_COMPLETE, PHASE_PAUSED, goal_view, render_blocker, round_prompt

from harness.loop.goal_ledger import BUDGET, COMPLETE, CONTINUE, NO_PROGRESS, STALL_ROUNDS, RoundFacts, call_signatures, fmt_tokens, settle_round, standing_of
from harness.loop.goal_proof import proven

logger = logging.getLogger("harness.manager")


class GoalDriverMixin:
    # -- goal rounds ------------------------------------------------------------
    def _schedule_goal_continue(self, session_id: str) -> None:
        """Request one driver pass for this session.

        Safe to call from any thread (it is reached through `mark_idle`, which every turn
        path ends on — WS, background delivery, durable resume). While the driver loop
        holds the in-flight mark, a request only sets the flag; the loop re-reads it
        after the spent attempt is released, which is what lets a goal run consecutive
        rounds with nobody typing.
        """
        if self.is_child_session(session_id):
            # A goal is a standing commitment of a WORKSPACE conversation — a child is
            # neither (a child session never surfaces one), and its idle
            # moment belongs to the parent's awaited `task` call.
            return
        if self._loop is None or self._loop.is_closed():
            return
        store = GoalStore.for_session(session_id)
        if store is None or not store.exists():
            return  # a session with no goal costs one stat() call
        self._goal_requested.add(session_id)
        if session_id in self._goal_inflight:
            return  # the driver is already draining; it will see the flag
        self._goal_inflight.add(session_id)
        asyncio.run_coroutine_threadsafe(self._goal_driver(session_id), self._loop)

    async def _goal_driver(self, session_id: str) -> None:
        """The serialized driver loop: one in-flight attempt per session.

        Rounds are serialized: a round's
        own idle edge arrives while this task holds the mark, so the next round is
        scheduled HERE — after `_continue_goal` released the spent attempt — instead of
        waiting for some later external idle event. Without this, consecutive rounds
        needed the user to type between them.
        """
        try:
            while session_id in self._goal_requested:
                self._goal_requested.discard(session_id)
                try:
                    await self._continue_goal(session_id)
                except Exception:
                    logger.exception("goal driver failed for %s", session_id)
        finally:
            self._goal_inflight.discard(session_id)
            if session_id in self._goal_requested:
                # A request that landed after the last loop check: hand it a fresh task
                # rather than dropping the edge.
                self._schedule_goal_continue(session_id)

    async def _continue_goal(self, session_id: str) -> None:
        """Open the next goal round, if one is owed.

        Runs at every idle; does nothing unless a goal is durably `active`, THIS process
        armed it, rounds remain, and the session is genuinely idle. A round is CHARGED at
        ADMISSION, not on success: the goal-attributed message is counted when it enters
        the log, so the cap and the live view count the
        round while it runs. The one rollback is a delivery that never happened
        (raced/busy/no engine) — a round that starts and then fails or is stopped keeps
        its charge, since only admission counts.
        """
        store = GoalStore.for_session(session_id)
        goal = store.load() if store is not None else None
        if goal is None or goal.phase != PHASE_ACTIVE or store.activation() != ACT_ARMED:
            return
        if goal.rounds_started >= goal.max_rounds:
            goal.phase = PHASE_BLOCKED
            goal.blocker = render_blocker(
                "round-limit",
                f"Goal reached its configured limit of {goal.max_rounds} rounds.",
            )
            goal.note = "round-limit"
            store.disarm()
            if store.save(goal):
                await self._note_goal(
                    session_id,
                    f"Goal blocked: the round limit was reached "
                    f"({goal.rounds_started} of {goal.max_rounds}). Tell me to continue "
                    "to raise it, or what to change.",
                    goal,
                    action="blocked",
                )
            return
        if self.is_running(session_id):
            return  # a person's turn holds the session; the next idle tries again
        if self._goal_awaits_background(session_id):
            # A background subagent of this session is still working, and its result
            # arrives as a message of its own. A round opened now can only wait: session
            # b3a8491e-9db spent six rounds (about 3.5M tokens) saying "the verify run is
            # still going". Not charged; the child's deregistration requests the next pass.
            return
        # The session is checkpointed BEFORE the round is reserved,
        # so a crash during the round cannot lose the state the round is about to build
        # on. Best-effort: a failed checkpoint costs nothing here (the round still runs).
        engine = self._engines.get(session_id)
        if engine is not None:
            try:
                self.save(session_id, engine)
            except Exception:
                logger.warning("goal checkpoint failed for %s", session_id, exc_info=True)
        round_no = goal.rounds_started + 1
        reservation = (goal.goal_id, goal.revision)
        # Re-validate the reservation immediately before the round runs, so an edit,
        # pause or clear that landed while this pass was being set up wins (a
        # revision fence). A stale reservation opens nothing.
        check = store.load()
        if (
            check is None
            or check.goal_id != reservation[0]
            or check.revision != reservation[1]
            or check.phase != PHASE_ACTIVE
            or store.activation() != ACT_ARMED
        ):
            return
        # -- admission: the round is charged BEFORE the turn is claimed -----------------
        # `rounds_started` is bumped when the goal message enters the log, so a running
        # round shows `N/M` immediately. The save bumps the revision; everything after
        # the round compares against THIS revision (the reservation above is the fence
        # for the setup window, this one is the fence for the run).
        previous_rounds = goal.rounds_started
        goal.rounds_started = round_no
        if not store.save(goal):
            return  # a write failure costs the round, never the work
        charged_revision = goal.revision
        await self._broadcast_goal(
            session_id, goal, action="round", activation_changed=False
        )
        outcome = await self.deliver_to_session(
            session_id,
            round_prompt(goal, round_no),
            source={
                "kind": "goal_round",
                "round": round_no,
                "goal_id": goal.goal_id,
                "revision": charged_revision,
            },
            steer_if_busy=False,
        )
        if outcome is None:
            # The round never started: raced with a person's turn, the session was busy,
            # or no engine could be built. Roll the admission charge back — the inherited
            # contract still holds: a busy yield does not consume a round. Guarded by the
            # charged revision, so an edit that landed in the same window owns the state.
            latest = store.load()
            if (
                latest is not None
                and latest.goal_id == reservation[0]
                and latest.revision == charged_revision
                and latest.rounds_started == round_no
            ):
                latest.rounds_started = previous_rounds
                if store.save(latest):
                    await self._broadcast_goal(
                        session_id,
                        latest,
                        action="round-rollback",
                        activation_changed=False,
                    )
            return
        latest = store.load()
        if latest is None or latest.goal_id != reservation[0]:
            return  # cleared or replaced while the round ran
        owned = latest.revision == charged_revision
        decision = CONTINUE
        if latest.phase != PHASE_COMPLETE:
            # The ledger: the round's spend, its changes and the checks'
            # standing are recorded whoever owns the lifecycle now — a paused goal still
            # spent what it spent — but only an owned, still-active round may end it.
            decision = self._settle_goal_round(session_id, latest, round_no, outcome)
            if not owned or latest.phase != PHASE_ACTIVE:
                decision = CONTINUE
            store.save(latest)
        if not owned:
            # The goal moved while the round ran (an edit, pause or clear, or the round's
            # own done/blocked): the new revision owns the lifecycle, and its own driver
            # pass decides what happens next. The round stays charged — it did start.
            return
        if decision != CONTINUE:
            await self._end_goal_by_ledger(session_id, store, latest, decision)
            return
        # A round the person stopped, or one that died on a provider failure, pauses the
        # goal. Cancellation never auto-restarts a round, and
        # abnormal failures wait for an explicit human-authorized resume. The charge
        # stands (only admission counts, never the outcome).
        if outcome in ("interrupted", "error"):
            latest.phase = PHASE_PAUSED
            latest.note = f"the last round ended: {outcome}"
            store.disarm()
            if store.save(latest):
                await self._note_goal(
                    session_id,
                    f"Goal paused: the last round ended as {outcome}. Tell me to "
                    "continue when you want it to carry on.",
                    latest,
                    action="pause",
                )

    def _goal_awaits_background(self, session_id: str) -> bool:
        """Whether a background subagent of this session is still running. Only subagents:
        each one runs on a clock, so waiting for it always ends, while a
        background shell may be a server that never exits."""
        with self._subagent_lock:
            return any(parent == session_id for parent in self._background_parents.values())

    def _edit_exempt_roots(self, session_id: str) -> list[str]:
        """The folders whose edits are not the work a check judges (the session's scratch folder when it
        works elsewhere): the same answer the `goal` tool's `done` reads, so the strip, the round judge and
        the gate cannot disagree about a passing check."""
        try:
            return list(self._engines[session_id].edit_exempt_roots())
        except Exception:
            return []  # no engine, or an older one: nothing is exempt, the conservative reading

    def _settle_goal_round(self, session_id: str, goal, round_no: int, outcome: str) -> str:
        """Record one finished round on `goal` from what the harness measured and return
        the ledger's decision (`goal_ledger.settle_round`). The engine facts were captured
        before the idle edge (`_drive_turn`); a round without them reads its transcript
        and charges no spend rather than guessing one."""
        capture = self._goal_round_facts.pop(session_id, None)
        messages = capture.messages if capture else self.session_messages(session_id)
        try:
            verifier = bool(getattr(self._engines.get(session_id), "goal_verifier", None)())
        except Exception:
            verifier = False  # an unanswerable question proves nothing
        facts = RoundFacts(
            round_no=round_no,
            outcome=str(outcome or ""),
            tokens=capture.tokens if capture else 0,
            changed=capture.changed if capture else [],
            standing=standing_of(goal, messages, self._edit_exempt_roots(session_id)),
            calls=capture.calls if capture else call_signatures(messages),
        )
        return settle_round(
            goal,
            facts,
            proven=proven(goal, messages, verifier=verifier, exempt=self._edit_exempt_roots(session_id)),
        )

    async def _end_goal_by_ledger(self, session_id: str, store, goal, decision: str) -> None:
        """Stop a goal the ledger judged finished, out of budget, or stalled — one
        sentence in the transcript either way, like every other stop."""
        if decision == COMPLETE:
            goal.phase = PHASE_COMPLETE
            goal.note = "proven by the checks"
            goal.blocker = None
            proof = (
                "every acceptance check passed with no file edited since"
                if goal.checks
                else "the verify subagent passed the work with nothing edited since"
            )
            text = f"Goal complete: {proof} (round {goal.rounds_started})."
            action = "done"
        elif decision == BUDGET:
            goal.phase = PHASE_BLOCKED
            goal.blocker = render_blocker(
                "budget",
                f"Goal spent its token budget ({fmt_tokens(goal.tokens_used)} of "
                f"{fmt_tokens(goal.token_budget)}).",
            )
            goal.note = "budget"
            text = (
                f"Goal blocked: it spent its token budget ({fmt_tokens(goal.tokens_used)} "
                f"of {fmt_tokens(goal.token_budget)}). Raise it to continue."
            )
            action = "blocked"
        else:
            goal.phase = PHASE_BLOCKED
            goal.blocker = render_blocker(
                NO_PROGRESS,
                f"{STALL_ROUNDS} rounds in a row made no new tool call, changed no file and "
                "moved no check.",
            )
            goal.note = NO_PROGRESS
            text = (
                f"Goal blocked: {STALL_ROUNDS} rounds in a row made no new tool call, "
                "changed no file and moved no check. Tell me what to change, or resume it "
                "to try again."
            )
            action = "blocked"
        store.disarm()
        if store.save(goal):
            await self._note_goal(session_id, text, goal, action=action)

    async def _note_goal(
        self, session_id: str, text: str, goal, *, action: str = "update"
    ) -> None:
        """Persist a one-line goal outcome and show it live. The notice is what a
        reloaded transcript reads (and what the GUI renders as a quiet line) — a goal
        that stops without saying so is indistinguishable from an agent that finished.
        The live broadcast carries the whole view (phase, activation, rounds, blocker),
        so the GoalBar moves without a refetch; the activation frame rides alongside."""
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.append_notice(
                "goal",
                text,
                status=goal.phase,
                rounds=goal.rounds_started,
                max_rounds=goal.max_rounds,
                objective=goal.objective,
            )
            self.save(session_id, engine)
        await self._broadcast_goal(session_id, goal, text=text, action=action, driver=True)

    def _goal_surface_view(self, session_id: str, goal, activation: str):
        """The goal as a surface shows it: the shared wire view plus each acceptance
        check's standing (`goal_proof.check_states`), read from the session's transcript the
        way the done-gate reads it. Surface-only - the model's `goal` tool keeps the plain
        view. A transcript that cannot be read leaves the states out rather than guessing."""
        view = goal_view(goal, activation)
        if view is None or not getattr(goal, "checks", None):
            return view
        try:
            from harness.loop.goal_proof import check_states

            view["check_status"] = check_states(
                goal, self.session_messages(session_id), self._edit_exempt_roots(session_id)
            )
        except Exception:
            pass
        return view

    async def _broadcast_goal(
        self,
        session_id: str,
        goal,
        *,
        text: str = "",
        action: str = "",
        activation_changed: bool = True,
        driver: bool = False,
    ) -> None:
        """Broadcast the goal's whole current view, plus activation when it moved.

        `driver=True` marks an outcome the driver PERSISTED as a transcript notice, so a
        client may append the quiet line live and replay it once from history. Bar-driven
        mutations omit it: they are state, not transcript events.
        """
        store = GoalStore.for_session(session_id)
        activation = store.activation() if store is not None else ACT_DISARMED
        view = self._goal_surface_view(session_id, goal, activation)
        await self.broadcast_session(
            session_id,
            {
                "type": "goal",
                "data": {"text": text, "action": action, "driver": driver, **(view or {})},
            },
        )
        if activation_changed:
            await self.broadcast_session(
                session_id,
                {
                    "type": "goal_activation",
                    "data": {
                        "goal_id": goal.goal_id if goal is not None else "",
                        "revision": goal.revision if goal is not None else 0,
                        "phase": goal.phase if goal is not None else "",
                        "activation": activation,
                    },
                },
            )
