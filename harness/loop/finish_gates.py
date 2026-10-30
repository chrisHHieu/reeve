"""FinishGatesMixin: what is checked before a turn may be called finished.

Three things are tracked over a turn: whether code changed, whether anything ran afterwards that
exercises the change, and whether the last check failed. They decide the note the model gets when it
is about to finish and the name of the turn's ending.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from harness.session.turns import ATTACHED_BY, TurnEndReason
from harness.tools.contract import safe_present_call

from .code_changes import CODE_SUFFIXES, CODE_WRITE_TOOLS, EVIDENCE_TOOLS, code_paths, exercises_change, is_code_write
from .nudges import (
    CHECK_FAILED_NUDGE_TEXT,
    CHECK_LOOP_NOTICE_AFTER,
    CHECK_LOOP_NOTICE_TEXT,
    CHECK_LOOP_STOP_AFTER,
)
from harness.loop.verification import last_verification, outcome_key
from harness.tools.checks import shown_command


class FinishGatesMixin:
    def _note_tool_outcome(self, name: str, arguments: Any, status: str) -> None:
        """Evidence for the verification gate, in execution order. A code write restarts the clock,
        and anything that executes afterwards counts as having exercised the change."""
        if status != "ok":
            return
        if name in CODE_WRITE_TOOLS and is_code_write(name, arguments):
            self._turn_wrote_code = True
            self._turn_ran_after = False
            self._turn_code_stems = getattr(self, "_turn_code_stems", set()) | {
                Path(path).stem.lower()
                for path in code_paths(name, arguments)
                if path.lower().endswith(CODE_SUFFIXES)
            }
        elif name in EVIDENCE_TOOLS and exercises_change(
            name, arguments, getattr(self, "_turn_code_stems", set()), self._script_folders()
        ):
            self._turn_ran_after = True

    def _script_folders(self) -> tuple[str, ...]:
        """Where a script a command names by a relative path is found: where the shell stands, then
        the workspace. Used to read whether that script imports a changed file."""
        folders = [getattr(getattr(self, "executor", None), "cwd", None)]
        permissions = getattr(self, "permissions", None)
        folders.append(str(permissions.workspace_root) if permissions is not None else None)
        return tuple(folder for folder in folders if folder)

    def _completion_reason(self) -> TurnEndReason:
        """How a finish is named; this is the verification gate's whole output.

        `truncated` wins over `unverified`, because a cut answer is a fact about the text the person
        actually received and the more urgent of the two. The unverified case is a turn that changed
        code and then ran nothing, with the gate's single note already sent and ignored. A failed
        last check with no edit since is its own fact: the check ran and the answer
        contradicts it, which is the state `completed` used to hide. The cheap way back to a plain
        `completed` is to run the check, or to change the code."""
        if self._turn_truncated:
            return TurnEndReason.COMPLETED_TRUNCATED
        if self._turn_wrote_code and not self._turn_ran_after:
            return TurnEndReason.COMPLETED_UNVERIFIED
        check = last_verification(self.messages)
        if check is not None and check.ok is False and check.edits_since == 0:
            return TurnEndReason.COMPLETED_CHECK_FAILED
        return TurnEndReason.COMPLETED

    def _attach_note(self, text: str, tag: str) -> None:
        self.messages.append({"role": "user", "content": text, ATTACHED_BY: tag, "ts": time.time()})

    def _nudge_failed_check(self) -> bool:
        """One note when the last check this session ran failed and no file has changed since.
        True when the caller should go around again.

        The one-shot rule lives in `_turn_check_nudged`. An unknown exit is not a failure, and an
        edit after the check may already be the fix, so both stay silent."""
        if self._turn_check_nudged or self._cancel.is_set() or self._turn_truncated:
            return False
        if "check_failed_nudge" in self.ablated:
            return False
        check = last_verification(self.messages)
        if check is None or check.ok is not False or check.edits_since != 0:
            return False
        self._turn_check_nudged = True
        self._attach_note(
            CHECK_FAILED_NUDGE_TEXT.format(
                command=shown_command(check.command) or "the last command", summary=check.summary or "it failed"
            ),
            "check_failed_nudge",
        )
        self.log.append("verify/nudge", turn=self._turn_no, step=self._step_no, reason="check_failed")
        return True

    def _track_check_streak(self) -> int:
        """Move the check-loop streak forward from the ledger.

        The repeat guard sees only byte-identical calls, and equivalent commands (`pytest -q`,
        `python -m pytest`) walk past it, so the signal comes from the ledger: a check result that is
        new (its transcript index moved), failed, with no edit after it. A pass, an unknown exit, an
        edit, or no check at all resets the streak, since a fix attempt is progress and the ledger
        already refuses to guess past an edit. It is called once per step boundary.

        Only a check that says the same thing as the last one advances the streak. A
        failed check with a different result is a new finding: an agent running one test file, then
        another, to learn what is broken. Counting those stopped a history-eval run mid-diagnosis
        (four failing checks, four different results), and every check-loop note in the eval and real
        logs had counted different results."""
        fact = last_verification(self.messages)
        if fact is None or fact.ok is not False or fact.edits_since > 0:
            self._check_streak = 0
            self._check_streak_outcome = None
            return 0
        if fact.index != self._check_streak_index:
            said = outcome_key(fact.summary)
            same = said == getattr(self, "_check_streak_outcome", None)
            self._check_streak = self._check_streak + 1 if same else 1
            self._check_streak_index = fact.index
            self._check_streak_outcome = said
        return self._check_streak

    def _check_loop_stopped(self) -> bool:
        """True when the check-loop streak has reached the stop threshold."""
        return "check_loop" not in self.ablated and self._check_streak >= CHECK_LOOP_STOP_AFTER

    def _nudge_check_loop(self) -> bool:
        """One model-visible note when the streak reaches the notice threshold.

        It has the shape of `_nudge_failed_check` (a user-role note tagged with `ATTACHED_BY` and
        logged as `verify/nudge {reason: "check_loop"}`), but it is bounded by the stop and not by
        once-per-turn silence: the note speaks once, and a streak that keeps climbing ends the turn on
        the checkpoint path."""
        if (
            self._check_loop_noticed
            or self._cancel.is_set()
            or "check_loop" in self.ablated
            or self._check_streak < CHECK_LOOP_NOTICE_AFTER
        ):
            return False
        fact = last_verification(self.messages)
        if fact is None or fact.ok is not False:
            return False
        self._check_loop_noticed = True
        self._attach_note(
            CHECK_LOOP_NOTICE_TEXT.format(
                count=self._check_streak,
                command=shown_command(fact.command) or "the check",
                summary=fact.summary or "it failed",
            ),
            "check_loop_nudge",
        )
        self.log.append(
            "verify/nudge",
            turn=self._turn_no,
            step=self._step_no,
            reason="check_loop",
            count=self._check_streak,
        )
        return True

    def _stuck_on(self) -> dict[str, Any]:
        """What the turn spent itself on, for the message shown when a cap trips.

        "Max iterations reached" names the counter that stopped the turn and gives the person
        nothing to act on. When one action was tried several times with the same effect, that action
        is the explanation, and the tool contract already knows how to say it in a sentence, which is
        why `present_call` exists. The check loop adds its own fact: which check, and how
        many times."""
        out: dict[str, Any] = {}
        worst = self.repeats.worst
        if worst is not None and worst.count >= 2:
            shown = safe_present_call(self.registry.contract(worst.tool), worst.tool, worst.arguments)
            out["repeated"] = {
                "tool": worst.tool,
                "count": worst.count,
                "summary": shown.summary if shown else worst.tool,
            }
        if self._check_loop_stopped():
            fact = last_verification(self.messages)
            command = {"command": shown_command(fact.command)} if fact is not None and fact.command else {}
            out["check_loop"] = {"count": self._check_streak, **command}
        return out
