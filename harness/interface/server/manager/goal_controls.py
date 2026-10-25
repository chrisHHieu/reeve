"""GoalControlsMixin — the goal as the human reads and steers it.

Split out of `wake.py` (size ceiling): the route's read (`goal_for_session`), the
`scheduling` gate on the arming actions, the configured default cap, and `goal_action`
(`POST /v1/sessions/{id}/goal`), the same state machine the model's tool drives.
"""
from __future__ import annotations

from harness.config.config import load_config
from harness.loop.goals import DEFAULT_MAX_ROUNDS, Goal, GoalStore, PHASE_ACTIVE, PHASE_COMPLETE, PHASE_PAUSED, clamp_rounds
from harness.modes.agents import get_agent
from typing import Any, Optional

from harness.loop.goal_ledger import budget_spent


def _tokens(value: Any) -> int:
    """A token budget from the route body: a non-negative integer, else 0 (none)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class GoalControlsMixin:
    # -- goal exposure (the human's read/control route + the GoalBar) -----------

    def goal_for_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """The current goal as the wire view, or None. Never raises."""
        store = GoalStore.for_session(session_id)
        if store is None:
            return None
        return self._goal_surface_view(session_id, store.load(), store.activation())

    def goal_persona_scheduling(self, session_id: str) -> bool:
        """Whether this session's persona opted into autonomously-opened turns.

        The model's `goal` tool is gated on the persona's `scheduling` trait at engine
        build (`agent.py`), and the human command arms the SAME driver — so the arming
        actions (`set`, `resume`) apply the gate server-side too. Pause, clear, edit and
        status stay available: they do not create continuation authority. A persona that
        cannot be resolved fails closed.
        """
        try:
            return bool(
                getattr(get_agent(), "scheduling", False)
            )
        except Exception:
            return False

    def _goal_default_rounds(self, session_id: str) -> int:
        """The session's resolved `[goal] max_rounds`.

        The built engine carries the config it was built with — the same number its
        `goal` tool applies. A session with no engine yet reads its workspace config
        (global merged in), so the human's first `set` lands on the configured default
        rather than the module one.
        """
        engine = self._engines.get(session_id)
        if engine is not None:
            value = getattr(engine, "goal_default_max_rounds", None)
            if isinstance(value, int) and value >= 1:
                return value
        record = self.session_store.load(session_id)
        workspace = record.workspace if record else None
        try:
            return int(load_config(workspace).goal_max_rounds)
        except Exception:
            return DEFAULT_MAX_ROUNDS

    async def goal_action(
        self,
        session_id: str,
        action: str,
        *,
        objective: Optional[str] = None,
        max_rounds: Optional[int] = None,
        reason: str = "",
        token_budget: Optional[int] = None,
    ) -> dict[str, Any]:
        """The HUMAN's controls (`POST /v1/sessions/{id}/goal`): set, pause, resume, edit,
        clear. This is the same state machine the model's tool drives, minus the model's
        restrictions: the user may resume a paused goal, and may edit a cap and resume
        after it was exhausted. The `/goal` command, as a route."""
        action = str(action or "").strip().lower()
        store = GoalStore.for_session(session_id)
        if store is None:
            return {"ok": False, "error": "no session"}
        current = store.load()
        if action == "clear":
            if current is None:
                return {"ok": True, "goal": None, "note": "there was no goal to clear"}
            store.clear()
            await self._broadcast_goal(session_id, None, action="clear")
            return {"ok": True, "goal": None, "note": "the goal was cleared"}
        if action == "set":
            new_objective = " ".join(str(objective or "").split())
            if not new_objective:
                return {"ok": False, "error": "set needs an objective"}
            if current is not None and current.phase != PHASE_COMPLETE:
                return {
                    "ok": False,
                    "error": (
                        f"a goal is already {current.phase}; use /goal edit <objective> "
                        "to change it or /goal clear before replacing it"
                    ),
                }
            if not self.goal_persona_scheduling(session_id):
                return {
                    "ok": False,
                    "error": (
                        "this persona has not opted into autonomous goal rounds; start "
                        "the goal from a scheduling persona instead"
                    ),
                }
            goal = Goal(
                objective=new_objective,
                max_rounds=clamp_rounds(
                    max_rounds or self._goal_default_rounds(session_id)
                ),
                token_budget=_tokens(token_budget),
            )
            if not store.save(goal):
                return {"ok": False, "error": "could not record the goal (storage failure)"}
            store.arm()
            await self._broadcast_goal(
                session_id, goal, text="Goal created.", action="set"
            )
            # An armed goal on an idle session owes its first round now — request a pass
            # the same way resume does; the driver yields if a turn holds the session.
            self._schedule_goal_continue(session_id)
            return {
                "ok": True,
                "goal": self._goal_surface_view(session_id, goal, store.activation()),
                "note": (
                    "the session will continue toward this objective on its own, one "
                    "round at a time, whenever it is idle"
                ),
            }
        if current is None:
            return {"ok": False, "error": "there is no goal in this session"}
        line = " ".join(str(reason or "").split())
        if action == "pause":
            current.phase = PHASE_PAUSED
            current.note = line or "paused"
            store.disarm()
        elif action == "resume":
            if current.phase == PHASE_COMPLETE:
                return {"ok": False, "error": "this goal is already complete"}
            if not self.goal_persona_scheduling(session_id):
                return {
                    "ok": False,
                    "error": (
                        "this persona has not opted into autonomous goal rounds; resume "
                        "it from a scheduling persona instead"
                    ),
                }
            if current.rounds_started >= current.max_rounds:
                return {
                    "ok": False,
                    "error": (
                        f"the goal used all {current.max_rounds} rounds; raise the round "
                        "limit first"
                    ),
                }
            if budget_spent(current):
                return {
                    "ok": False,
                    "error": "the goal spent its token budget; raise the budget first",
                }
            current.phase = PHASE_ACTIVE
            current.note = ""
            # A resume is the person's "try again": the stall count starts over, so a
            # goal blocked `no-progress` gets its full allowance back.
            current.stalled_rounds = 0
            store.arm()
        elif action == "edit":
            new_objective = " ".join(str(objective or "").split())
            if not new_objective and not max_rounds and token_budget is None:
                return {
                    "ok": False,
                    "error": "edit needs objective, max_rounds and/or token_budget",
                }
            if token_budget is not None:
                current.token_budget = _tokens(token_budget)
            if new_objective:
                current.objective = new_objective
            if max_rounds:
                current.max_rounds = clamp_rounds(max_rounds)
        else:
            return {
                "ok": False,
                "error": f"unknown action {action!r}; use set, pause, resume, edit or clear",
            }
        if not store.save(current):
            return {"ok": False, "error": "could not record the goal change (storage failure)"}
        if action == "pause" and self.is_running(session_id):
            # A human pause stops goal execution NOW: abort the live turn so the model
            # cannot keep acting on the old objective (the driver aborts with
            # `keepInbox`; this harness has no inbox to discard, so the interrupt is the
            # whole of it). The round's own outcome path sees the revision change and
            # leaves the paused state alone.
            engine = self._engines.get(session_id)
            if engine is not None:
                try:
                    self._interrupt_engine(session_id, engine)
                except Exception:
                    pass
        text = {
            "pause": "Goal paused.",
            "resume": "Goal resumed.",
            "edit": "Goal updated.",
        }.get(action, "")
        await self._broadcast_goal(session_id, current, text=text, action=action)
        if action == "resume":
            # The next idle opens the round; request a pass now too, so an idle session
            # does not wait for an unrelated event.
            self._schedule_goal_continue(session_id)
        return {"ok": True, "goal": self._goal_surface_view(session_id, current, store.activation())}
