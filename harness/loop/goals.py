"""Same-session goal continuation — the goal-round driver.

A **goal** is an explicit, durable "keep working on this objective" record. While one is
armed and its session is idle, the runtime opens the next round as an ordinary turn in
the SAME session, carrying one fixed `<goal_round>` prompt that names the objective and
the round number. Every other cross-turn bound in the harness counts *within* a turn
(`max_iterations`, the repeat guard, the token budget), so this module owns the ones that
must count across rounds:

    rounds     the cap the goal was created with. Exhaustion is a blocker with the
               stable code `round-limit`, not a silent stop.
    idle-only  a round starts only when the session is idle, so a person's turn always
               wins; work that arrives first makes the goal yield until the next idle.
    no-revival a restart never re-arms and never rewrites the durable phase. Activation
               is process-local (the `_ACTIVATION` registry below), so a fresh process
               starts every goal disarmed and only an explicit resume — the human's, or
               a resume the model asked the user for — continues it. A goal that resumes
               itself after a crash is a loop nobody authorized.

**Phase vs activation**: the durable `phase`
(`active | paused | blocked | complete`) records what happened to the objective; the
process-local `armed | disarmed` records whether THIS process may open another round.
Restarting disarms without touching the phase, so a reopened session shows "Inactive
Goal" over the objective it was pursuing — not a rewritten history.

Storage is one small JSON file per session under the state dir, rewritten on change.
Best-effort like the other stores: a missing or broken file reads as "no goal" rather
than failing a turn, and a write failure costs the round, never the work.

Three parts: the state, the round driver and
the model's controls, with a JSON file
instead of a log projection.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from aisuite.agents import ToolMetadata, tool

from .goal_ledger import budget_spent, clean_ledger
from .goal_proof import clean_checks, completion_gaps, proof_instruction
from .goal_prompts import round_prompt, wrapup_instruction

logger = logging.getLogger(__name__)

#: Durable lifecycle phase. What happened to the objective.
PHASE_ACTIVE = "active"
PHASE_PAUSED = "paused"
PHASE_BLOCKED = "blocked"
PHASE_COMPLETE = "complete"

#: Process-local continuation eligibility. Never persisted.
ACT_ARMED = "armed"
ACT_DISARMED = "disarmed"

#: The words the first iteration of this store persisted under `status` (armed | paused |
#: completed | blocked). Read once on load so a dev state dir from before the
#: phase/activation split still reads as a goal rather than a broken file.
_LEGACY_PHASE = {
    "armed": PHASE_ACTIVE,
    "active": PHASE_ACTIVE,
    "paused": PHASE_PAUSED,
    "blocked": PHASE_BLOCKED,
    "completed": PHASE_COMPLETE,
    "complete": PHASE_COMPLETE,
}

#: Rounds a goal gets unless the model, the person or `[goal] max_rounds` asks for
#: another number.
DEFAULT_MAX_ROUNDS = 256
#: The hard ceiling a caller cannot exceed: a positive
#: safe integer, with no tighter upper bound,
#:; `Number.isSafeInteger`), so the ceiling is
#: `Number.MAX_SAFE_INTEGER`. A goal is autonomous spend; this is the same one number
#: that bounds it.
MAX_MAX_ROUNDS = 2**53 - 1
#: The model may not report `blocked` before this many admitted rounds have run
#: One bad round is not a blocker.
BLOCKED_AFTER_ROUNDS = 3

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_BLOCKER_CODE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: Process-local activation, keyed by the goal file's path. A module-level registry is
#: what "process-local" means here: `GoalStore.for_session` hands out a fresh wrapper
#: per call, while the tool, the driver and the routes must all see the same activation.
_ACTIVATION: dict[str, str] = {}


def render_blocker(code: str, message: str) -> dict[str, str]:
    """One structured blocker: a lower-kebab `code` plus the sentence a person reads."""
    code = str(code or "").strip()
    if not _BLOCKER_CODE.match(code):
        code = "blocked"
    return {"code": code, "message": " ".join(str(message or "").split()) or "blocked"}


def goal_view(goal: Optional["Goal"], activation: str) -> Optional[dict[str, Any]]:
    """The wire shape shared by the tool, the route and the broadcasts."""
    if goal is None:
        return None
    return {
        "goal_id": goal.goal_id,
        "objective": goal.objective,
        "phase": goal.phase,
        "activation": activation,
        "rounds_started": goal.rounds_started,
        "max_rounds": goal.max_rounds,
        "revision": goal.revision,
        "note": goal.note,
        "blocker": dict(goal.blocker) if goal.blocker else None,
        "updated_at": goal.updated_at,
        "checks": list(goal.checks),
        "token_budget": goal.token_budget,
        "tokens_used": goal.tokens_used,
        "stalled_rounds": goal.stalled_rounds,
    }


def _count(value: Any) -> int:
    """A stored non-negative integer; anything unusable reads as 0."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def clamp_rounds(value: Any, default: int = DEFAULT_MAX_ROUNDS) -> int:
    """The one place a round cap is bounded: `default` when unusable, else
    `[1, MAX_MAX_ROUNDS]`."""
    try:
        rounds = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_MAX_ROUNDS, rounds))


@dataclass
class Goal:
    objective: str
    phase: str = PHASE_ACTIVE
    rounds_started: int = 0
    max_rounds: int = DEFAULT_MAX_ROUNDS
    goal_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    #: Bumped on every durable change; the round driver reserves `{goal_id, revision}`
    #: and re-validates it before a round runs, so an edit/pause/clear invalidates a
    #: reservation taken against the old state.
    revision: int = 1
    note: str = ""
    #: The structured blocker (`{code, message}`), present exactly while blocked.
    blocker: Optional[dict[str, str]] = None
    updated_at: float = field(default_factory=time.time)
    #: Acceptance checks: shell commands whose passing run proves the objective. `done`
    #: is refused until each has passed with no file edited since (`completion_gaps`).
    checks: list[str] = field(default_factory=list)
    #: The harness's own accounting (`goal_ledger`): provider tokens the rounds
    #: spent, the cap on them (0 = none), consecutive rounds that moved nothing, and one
    #: entry per settled round.
    token_budget: int = 0
    tokens_used: int = 0
    stalled_rounds: int = 0
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["Goal"]:
        if not isinstance(raw, dict) or not str(raw.get("objective", "")).strip():
            return None
        try:
            rounds = max(0, int(raw.get("rounds_started", 0) or 0))
            cap = clamp_rounds(raw.get("max_rounds", DEFAULT_MAX_ROUNDS))
        except (TypeError, ValueError):
            return None
        phase = str(raw.get("phase") or raw.get("status") or PHASE_ACTIVE).strip()
        if phase not in (PHASE_ACTIVE, PHASE_PAUSED, PHASE_BLOCKED, PHASE_COMPLETE):
            phase = _LEGACY_PHASE.get(phase, PHASE_PAUSED)  # unknown is not permission
        blocker = raw.get("blocker")
        if phase != PHASE_BLOCKED or not isinstance(blocker, dict):
            blocker = None
        else:
            blocker = render_blocker(
                str(blocker.get("code", "")), str(blocker.get("message", ""))
            )
        return cls(
            objective=str(raw["objective"]).strip(),
            phase=phase,
            rounds_started=rounds,
            max_rounds=cap,
            goal_id=str(raw.get("goal_id", "")) or uuid.uuid4().hex[:12],
            revision=int(raw.get("revision", 1) or 1),
            note=str(raw.get("note", "")),
            blocker=blocker,
            updated_at=float(raw.get("updated_at", 0.0) or 0.0),
            checks=clean_checks(raw.get("checks")),
            token_budget=_count(raw.get("token_budget")),
            tokens_used=_count(raw.get("tokens_used")),
            stalled_rounds=_count(raw.get("stalled_rounds")),
            ledger=clean_ledger(raw.get("ledger")),
        )


class GoalStore:
    """One session's goal record + its process-local activation. Nothing raises."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @classmethod
    def for_session(cls, session_id: Optional[str]) -> Optional["GoalStore"]:
        if not session_id:
            return None
        from harness.safety.secrets import state_dir

        name = _UNSAFE.sub("-", str(session_id)).strip("-") or "session"
        return cls(state_dir() / "goals" / f"{name}.json")

    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False

    def load(self) -> Optional[Goal]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("goal file %s is unreadable; treating it as absent", self.path.name)
            return None
        return Goal.from_dict(raw)

    def save(self, goal: Goal) -> bool:
        goal.updated_at = time.time()
        goal.revision += 1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(goal.as_dict(), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            logger.warning("could not write goal file %s", self.path.name, exc_info=True)
            return False

    def clear(self) -> None:
        """Delete the goal (a JSON store needs no tombstone)."""
        try:
            self.path.unlink()
        except OSError:
            pass
        self.forget_activation()

    # -- process-local activation ----------------------------------------------

    def activation(self) -> str:
        return _ACTIVATION.get(str(self.path), ACT_DISARMED)

    def set_activation(self, activation: str) -> None:
        if activation == ACT_ARMED:
            _ACTIVATION[str(self.path)] = ACT_ARMED
        else:
            _ACTIVATION.pop(str(self.path), None)

    def arm(self) -> None:
        self.set_activation(ACT_ARMED)

    def disarm(self) -> None:
        self.set_activation(ACT_DISARMED)

    def forget_activation(self) -> None:
        _ACTIVATION.pop(str(self.path), None)

    @classmethod
    def disarm_all(cls) -> int:
        """Disarm every goal in this process — activation only.

        Called once when the process starts, and the durable phase is NOT rewritten:
        a goal that was `active` when the app closed reads `active`/`disarmed` after a
        restart (the bar's "Inactive Goal"), and only an explicit resume re-arms it.
        Returns how many entries were armed. The rule: a driver
        mounted over existing state never inherits hidden automatic authority.
        """
        armed = sum(1 for value in _ACTIVATION.values() if value == ACT_ARMED)
        _ACTIVATION.clear()
        return armed


# -- the model's controls ---------------------------------------------------------

#: The actions that are a PERSON's decision: what the goal is, what proves it,
#: and whether it runs. In the real store the model called `edit` 7 times; 3 of them were in
#: turns no person opened (a goal round, two background-result turns), and each rewrote the
#: objective and two of them the acceptance checks — the worker moving its own finish line.
PERSON_ACTIONS = frozenset({"set", "edit", "pause", "resume", "clear"})


_GOAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "goal",
        "description": (
            "Manage this session's goal: an objective the session keeps working toward "
            "on its own. While a goal is armed, the harness opens a new turn (a 'round') "
            "whenever the session is idle, until the goal is done, blocked, paused or out "
            "of rounds. Set one when the user's own request is a long-running objective "
            "that outlasts this turn — in any language, even without the word \"goal\" "
            "(\"keep going until every test passes\", a migration, a build in parts). "
            "Never for work this turn can finish, a question, or on your own initiative. "
            "Only a turn the user opened may set, edit, pause, resume or clear; a round "
            "may only report done or blocked. Run the acceptance checks before done; "
            f"blocked is for a condition that held {BLOCKED_AFTER_ROUNDS} rounds and only "
            "the user can clear. An action that is not allowed is refused with the reason. "
            "When done or blocked ends the run, close with a short summary for the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "set",
                        "status",
                        "pause",
                        "resume",
                        "edit",
                        "clear",
                        "done",
                        "blocked",
                    ],
                    "description": (
                        "set: arm a new objective (requires `objective`). status: read "
                        "the current goal. pause: stop opening rounds until resumed. "
                        "resume: re-arm a disarmed or blocked goal (ask the user first — "
                        "a goal the user paused can only be resumed by the user). edit: "
                        "change the objective and/or `max_rounds`, keeping the rounds "
                        "already run. clear: delete the goal. done: the objective is "
                        "complete. blocked: only the user can unblock (requires "
                        "`reason`)."
                    ),
                },
                "objective": {
                    "type": "string",
                    "description": "What 'done' means, in the user's terms (set/edit).",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": (
                        f"How many rounds the goal may open (default "
                        f"{DEFAULT_MAX_ROUNDS}; set/edit)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why (for done/blocked/pause) — one line the user reads.",
                },
                "checks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Acceptance checks (set/edit): shell commands whose passing run "
                        "proves the objective, such as the project's own test, type-check "
                        "or lint command. Name the ones the user asked for or the project "
                        "already uses; run them exactly as written. edit replaces the list."
                    ),
                },
                "token_budget": {
                    "type": "integer",
                    "description": (
                        "Provider tokens the goal's rounds may spend in all (set/edit; "
                        "0 = no budget). Set it only when the user names one."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


def goal_tools(
    store: GoalStore,
    *,
    round_of: Optional[Callable[[], int]] = None,
    blocked_after: int = BLOCKED_AFTER_ROUNDS,
    default_max_rounds: int = DEFAULT_MAX_ROUNDS,
    messages_of: Optional[Callable[[], Any]] = None,
    verifier_of: Optional[Callable[[], bool]] = None,
    exempt_of: Optional[Callable[[], Sequence[str]]] = None,
    person_turn_of: Optional[Callable[[], bool]] = None,
) -> list:
    """The `goal` tool bound to one session's store.

    `round_of()` returns the round number of the turn making the call (0 outside a
    goal round) — the completion authority: the blocked threshold only binds the
    model inside an autonomous round, while a direct human request to block is not
    second-guessed by a count.

    `default_max_rounds` is the resolved `[goal] max_rounds` for the session's config
    — the cap `set` applies when neither the model nor the
    person names one. It is clamped here, so a hand-built caller cannot exceed the
    ceiling either.

    `messages_of()` returns the session's transcript; `done` reads its proof there
    (`completion_gaps`). Without one (a bare caller) there is nothing to read, and
    `done` is taken at its word as before. `verifier_of()` says whether this session
    can ask the `verify` subagent right now (the task tool is registered and the agent
    is not disabled); only then does a check-less goal need its verdict.

    `person_turn_of()` says whether a PERSON opened the turn making the call (the
    person's direct authority). `set`, `edit`, `pause`, `resume` and `clear` are the
    person's decisions and are refused in a turn the harness opened; `done` and `blocked`
    stay open to every turn, each behind its own proof. Without one (a bare caller) there
    is no turn to ask about and nothing is refused, as before.
    """
    cap_default = clamp_rounds(default_max_rounds)

    def _verifier() -> bool:
        try:
            return bool(verifier_of()) if verifier_of is not None else False
        except Exception:
            return False  # an unanswerable question never blocks done

    def _round() -> int:
        try:
            return max(0, int(round_of() if round_of is not None else 0))
        except Exception:
            return 0

    def _person_turn() -> bool:
        if person_turn_of is None:
            return True
        try:
            return bool(person_turn_of())
        except Exception:
            return False  # an unanswerable question is not a person's say-so

    def goal(
        action: str,
        objective: str = "",
        max_rounds: int = 0,
        reason: str = "",
        checks: Any = None,
        token_budget: int = 0,
    ) -> dict[str, Any]:
        """Manage this session's goal (see the schema description).

        Args:
            action: set | status | pause | resume | edit | clear | done | blocked
            objective: what "done" means (set/edit)
            max_rounds: rounds the goal may open (set/edit)
            reason: one line for done/blocked/pause
            checks: acceptance commands (set/edit)
            token_budget: provider tokens the rounds may spend (set/edit; 0 = none)
        """
        action = str(action or "").strip().lower()
        current = store.load()
        line = " ".join(str(reason or "").split())

        if action == "status":
            return {"ok": True, "goal": goal_view(current, store.activation())}
        if action in PERSON_ACTIONS and not _person_turn():
            return {
                "error": (
                    f"action={action} needs a turn the user opened. The harness opened "
                    "this one (a goal round, a wake, a background result or a scheduled "
                    "run), where the goal's objective, checks and state are not yours to "
                    "change. Report done or blocked here, or say what you would change "
                    "and let the user decide."
                )
            }
        if action == "clear":
            if current is None:
                return {"ok": True, "goal": None, "note": "there was no goal to clear"}
            store.clear()
            return {"ok": True, "goal": None, "note": "the goal was cleared"}
        if action == "set":
            objective = " ".join(str(objective or "").split())
            if not objective:
                return {"error": "action=set needs an objective"}
            if current is not None and current.phase != PHASE_COMPLETE:
                return {
                    "error": (
                        f"a goal is already {current.phase}; use action=edit to change "
                        "it, or pause or clear it first"
                    )
                }
            new = Goal(
                objective=objective,
                checks=clean_checks(checks),
                max_rounds=clamp_rounds(max_rounds or cap_default, cap_default),
                token_budget=_count(token_budget),
            )
            if not store.save(new):
                return {"error": "could not record the goal (storage failure)"}
            store.arm()
            return {
                "ok": True,
                "goal": goal_view(new, store.activation()),
                "note": (
                    "the session will continue toward this objective on its own, one "
                    "round at a time, whenever it is idle. Tell the user in one line "
                    "that you set this goal and that Stop or /goal pause ends it. "
                    + proof_instruction(new)
                ),
            }
        if current is None:
            return {"error": f"there is no goal in this session to {action}"}

        wrapup = ""
        if action == "pause":
            current.phase = PHASE_PAUSED
            current.note = line or "paused"
            store.disarm()
        elif action == "resume":
            if current.phase == PHASE_COMPLETE:
                return {"error": "this goal is already complete; set a new one instead"}
            if current.phase == PHASE_PAUSED:
                return {
                    "error": (
                        "the model cannot resume a paused goal; the user must resume it"
                    )
                }
            if current.rounds_started >= current.max_rounds:
                return {
                    "error": (
                        f"the goal used all {current.max_rounds} rounds; use action=edit "
                        "to raise max_rounds first"
                    )
                }
            if budget_spent(current):
                return {"error": "the goal spent its token_budget; use action=edit to raise it first"}
            current.phase = PHASE_ACTIVE
            current.stalled_rounds = 0
            current.note = ""
            store.arm()
        elif action == "edit":
            objective = " ".join(str(objective or "").split())
            if not objective and not max_rounds and checks is None and not token_budget:
                return {"error": "action=edit needs objective, max_rounds, checks and/or token_budget"}
            if token_budget:
                current.token_budget = _count(token_budget)
            if objective:
                current.objective = objective
            if max_rounds:
                current.max_rounds = clamp_rounds(max_rounds)
            if checks is not None:
                current.checks = clean_checks(checks)
        elif action == "done":
            gaps = (
                completion_gaps(
                    current,
                    messages_of(),
                    verifier=_verifier(),
                    exempt=exempt_of() if exempt_of is not None else (),
                )
                if messages_of is not None
                else []
            )
            if gaps:
                # Refused, not recorded: the goal stays active and armed, and the result
                # says exactly what would prove it (an agent-oriented error — lecture 10).
                return {
                    "error": "done refused — the work is not proven yet: " + "; ".join(gaps),
                    "unmet": gaps,
                    "next": "run the check(s) above, fix what fails, then call done again",
                }
            current.phase = PHASE_COMPLETE
            current.note = line or "completed"
            current.blocker = None
            store.disarm()
            wrapup = wrapup_instruction(current)
        elif action == "blocked":
            if not line:
                return {"error": "action=blocked needs a reason"}
            round_no = _round()
            if round_no > 0 and current.rounds_started < max(1, int(blocked_after)):
                return {
                    "error": (
                        f"blocked requires at least {max(1, int(blocked_after))} "
                        f"consecutive goal rounds; the current round is "
                        f"{current.rounds_started}"
                    )
                }
            current.phase = PHASE_BLOCKED
            current.note = line
            current.blocker = render_blocker("model-reported", line)
            store.disarm()
            wrapup = wrapup_instruction(current, blocked_reason=line)
        else:
            return {
                "error": (
                    f"unknown action {action!r}; use set, status, pause, resume, edit, "
                    "clear, done or blocked"
                )
            }
        if not store.save(current):
            return {"error": "could not record the goal change (storage failure)"}
        out: dict[str, Any] = {"ok": True, "goal": goal_view(current, store.activation())}
        if wrapup:
            out["wrapup"] = wrapup
        elif action in ("pause", "resume", "edit"):
            out["note"] = current.note
        return out

    goal.__name__ = "goal"
    goal.__doc__ = _GOAL_SCHEMA["function"]["description"]
    # The schema is per-session only in the default it advertises: the model must read
    # the configured `[goal] max_rounds`, not the module default, when it says what
    # happens if it omits `max_rounds`.
    schema = copy.deepcopy(_GOAL_SCHEMA)
    schema["function"]["parameters"]["properties"]["max_rounds"]["description"] = (
        f"How many rounds the goal may open (default {cap_default}, "
        f"max {MAX_MAX_ROUNDS}; set/edit)."
    )
    wrapped = tool(
        goal,
        metadata=ToolMetadata(
            category="planning",
            risk_level="low",
            capabilities=["goal"],
            description=_GOAL_SCHEMA["function"]["description"],
        ),
    )
    wrapped.__tool_schema__ = schema
    return [wrapped]


__all__ = [
    "ACT_ARMED",
    "ACT_DISARMED",
    "BLOCKED_AFTER_ROUNDS",
    "DEFAULT_MAX_ROUNDS",
    "Goal",
    "GoalStore",
    "MAX_MAX_ROUNDS",
    "PHASE_ACTIVE",
    "PHASE_BLOCKED",
    "PHASE_COMPLETE",
    "PHASE_PAUSED",
    "clamp_rounds",
    "goal_tools",
    "goal_view",
    "render_blocker",
    "round_prompt",
    "wrapup_instruction",
]
