"""RepairMixin — a repair the session just proved is written down, and one this project made
before is recalled when its failure comes back.

Both happen at a step boundary, off the same fact the check loop reads there: the last
check in the transcript. Neither asks a model anything.

- **Record.** The last check no longer names a failure its previous run named, and work was
  edited between the two (`repair_facts.repair_of`). The project's repairs store
  (`memory/repairs.py`) keeps which failures, which files, how many steps. Logged as
  `repair/fixed`.
- **Recall.** The last check failed and names a failure the store holds a repair of, made
  in another turn or another session. One model-visible note says where that repair was,
  what it changed (a short diff the harness took from the undo store's before-image of its
  first edit against the file as the green check left it), and what became of those files
  since: back as before the repair, changed some other way, or gone (`ATTACHED_BY:
  repair_recall`, logged as `repair/recalled`). Once a turn for each failure; never an
  instruction.
- **Ask.** A repair that took `COSTLY_STEPS` or more, for a failure no lesson is yet saved
  with, in a session whose memory may be written: one note asks for the cause as a `lesson`
  memory, or nothing (`ATTACHED_BY: repair_lesson`, `asked` on the `repair/fixed` row).
  Once a turn. The model writes it through the memory door or does not; nothing checks
  that it did, and nothing runs after the turn.

A session built without a project memory directory has no store and none of this runs.
Everything here is best-effort: a store that cannot be read or written never costs a turn.
"""
from __future__ import annotations

import logging

import time
from harness.session.turns import ATTACHED_BY
from typing import Any

import os
from pathlib import Path

from harness.context.memory.anchors import digest as _digest
from harness.context.memory.repairs import ABSENT, COSTLY_STEPS, change_between as _change_between, lesson_request as _lesson_request, note as _repair_note
from harness.loop.repair_facts import Check, last_check, repair_of

logger = logging.getLogger(__name__)


class RepairMixin:
    def _track_repairs(self) -> None:
        """Called once per step boundary, before the next request is built."""
        store = getattr(self, "repairs", None)
        if store is None:
            return
        try:
            check = last_check(self.messages)
            key = check.key if check is not None else None
            if not self._repair_primed:
                # The first look of this engine's life. A resumed session's last check was
                # dealt with by the process that ran it; only what lands from here on is new.
                self._repair_primed, self._repair_seen = True, key
                return
            if check is None or key == self._repair_seen:
                return
            self._repair_seen = key
            self._record_repair(store)
            if check.ok is False and check.marks:
                self._recall_repairs(store, check)
        except Exception:  # noqa: BLE001 — experience is never worth a turn
            logger.debug("repair tracking failed", exc_info=True)

    def _what_the_repair_changed(self, store: Any, repair: Any) -> tuple[dict[str, str], dict[str, str]]:
        """Each edited file as it was before the repair, and what the repair changed in it:
        the before-image the undo store took at the repair's first edit to the file (its
        digest, `ABSENT` when that edit created it) against the file as the green check left
        it (`change_between`). A file with no image (no undo store, pruned, edited by a shell
        command) has neither, and is left out. What cannot be read here costs those, never
        the record."""
        undo = getattr(self, "undo", None)
        if undo is None:
            return {}, {}
        before: dict[str, str] = {}
        changes: dict[str, str] = {}
        for raw, call_id in repair.first_edits:
            try:
                target = store.resolve(raw)
                if target is None:
                    continue
                for shot in undo.images(call_id):
                    if os.path.normcase(str(Path(shot.path).resolve())) != os.path.normcase(str(Path(target).resolve())):
                        continue
                    said = _digest(Path(shot.saved_to)) if shot.existed else ABSENT
                    if said:
                        before[raw] = said
                    was = Path(shot.saved_to).read_bytes() if shot.existed else None
                    now = Path(target).read_bytes() if Path(target).is_file() else None
                    change = _change_between(was, now)
                    if change:
                        changes[raw] = change
            except (OSError, ValueError):
                logger.debug("no before-image for a repaired file", exc_info=True)
        return before, changes

    def _record_repair(self, store: Any) -> None:
        repair = repair_of(self.messages)
        if repair is None:
            return
        before, changes = self._what_the_repair_changed(store, repair)
        store.record(repair.marks, repair.command, repair.files, repair.steps, before=before, changes=changes)
        # A failure this session was told about, repaired again: the two costs side by side
        # are the only measure of whether being told helped.
        earlier = [self._recalled_steps.pop(mark) for mark in repair.marks if mark in self._recalled_steps]
        asked = self._ask_for_lesson(store, repair)
        self.log.append(
            "repair/fixed",
            turn=self._turn_no,
            step=self._step_no,
            marks=len(repair.marks),
            files=len(repair.files),
            before=len(before),
            diffs=len(changes),
            steps=repair.steps,
            **({"recalled": True, "earlier_steps": min(earlier)} if earlier else {}),
            **({"asked": True} if asked else {}),
        )

    def _ask_for_lesson(self, store: Any, repair: Any) -> bool:
        """One note, once a turn, when a repair cost enough that its cause is worth keeping
        and nothing has kept it yet. The model decides whether there is anything to say."""
        if (
            repair.steps < COSTLY_STEPS
            or self._turn_lesson_asked
            or self._cancel.is_set()
            or not store.asks()
            or store.has_lesson(repair.marks)
        ):
            return False
        self._turn_lesson_asked = True
        self.messages.append(
            {
                "role": "user",
                "content": _lesson_request(repair.marks, repair.steps),
                ATTACHED_BY: "repair_lesson",
                "ts": time.time(),
            }
        )
        return True

    def _recall_repairs(self, store: Any, check: Check) -> bool:
        if "repair_recall" in self.ablated or self._cancel.is_set():
            return False
        marks = check.marks - self._turn_recalled
        found = store.recall(marks) if marks else []
        if not found:
            return False
        self._turn_recalled |= check.marks
        shared = {mark for row in found for mark in row["shared"]}
        for row in found:
            for mark in row["shared"]:
                self._recalled_steps.setdefault(mark, int(row.get("steps") or 0))
        said = _repair_note(found)
        self.messages.append(
            {
                "role": "user",
                "content": said,
                ATTACHED_BY: "repair_recall",
                "ts": time.time(),
            }
        )
        self.log.append(
            "repair/recalled",
            turn=self._turn_no,
            step=self._step_no,
            marks=len(shared),
            repairs=len(found),
            undone=sum(len(row["undone"]) for row in found),
            diffs=said.count("```diff"),
            changed=sum(len(row["changed"]) + len(row["gone"]) for row in found),
        )
        store.told(found)
        return True
