"""NoticesMixin: display-only markers in the history, and the receipt a turn ends with."""

from __future__ import annotations

import time
from typing import Any, Optional

from harness.session.turns import is_user_turn


class NoticesMixin:
    # Notices that record an ambient change and not an outcome. They may be appended after a failed
    # turn, and none of them is a reason for the retry to stop being offered: switching models and
    # then retrying is the intended recovery, and re-arming MCP and then retrying is the same move
    # one surface over.
    #
    # `tools_changed` is no longer written (see `_note_tool_changes`) but stays in the set. Sessions
    # saved before 2026-09-10 still carry one, and taking it out would put a retriable error's Retry
    # button out of reach on exactly those transcripts. `changes` is the turn's receipt, written
    # after a failed turn's error notice, and the receipt of a failed turn must not take its Retry
    # away.
    _AMBIENT_NOTICES = frozenset({"model_switch", "thinking_switch", "tools_changed", "changes"})

    def _tail_is_retriable_error(self) -> bool:
        """True when the history tail is an error notice, looking through the ambient notices added
        after it, since none of them may consume the retry."""
        for message in reversed(self.messages):
            if message.get("role") != "notice":
                return False
            if message.get("kind") in self._AMBIENT_NOTICES:
                continue
            return message.get("kind") == "error"
        return False

    def append_notice(self, kind: str, text: Optional[str] = None, **fields: Any) -> None:
        """The public marker writer, for things that happen outside a turn (the goal driver parking a
        goal at its cap). The notice is saved, logged, and dropped from every provider feed exactly
        like the ones the loop writes, because the model must never read the harness talking to the
        person."""
        self._append_notice(kind, text, **fields)

    def _append_notice(self, kind: str, text: Optional[str] = None, **fields: Any) -> None:
        """Save a marker (an error, an interruption) as a display-only `notice` message. It survives
        a reload like the transcript does, while `_outbound_messages` drops the role so no provider
        sees it. Extra `fields` (the name of the failing MCP server, say) are saved on the message
        for structured rendering."""
        notice: dict[str, Any] = {"role": "notice", "kind": kind, "ts": time.time()}
        if text:
            notice["text"] = text
        extra = {key: value for key, value in fields.items() if value is not None}
        notice.update(extra)
        self.messages.append(notice)
        self.log.append("notice", turn=self._turn_no, step=self._step_no, notice_kind=kind, text=text, **extra)

    def _notes_among(self, paths: list[str]) -> list[str]:
        """The paths among `paths` that sit in one of the agent's memory folders."""
        folders = list(getattr(getattr(self, "permissions", None), "memory_dirs", None) or [])
        if not folders or not paths:
            return []
        from pathlib import Path

        roots = []
        for folder in folders:
            try:
                roots.append(Path(folder).expanduser().resolve())
            except (OSError, RuntimeError):
                continue
        out: list[str] = []
        for raw in paths:
            try:
                resolved = Path(raw).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if any(resolved == root or root in resolved.parents for root in roots) and raw not in out:
                out.append(raw)
        return out

    def _changes_payload(self) -> dict[str, Any]:
        """The turn's receipt, or nothing at all.

        It is absent and not empty on purpose. A surface tests for presence, and "this turn changed
        nothing" is not a receipt: it would be a line after every question the agent merely answered."""
        if self.changes is None:
            return {}
        summary = self.changes.summary()
        if not summary:
            return {}
        receipt = summary.as_dict()
        # The check that proves the change, read from its own exit and summary and not from the
        # answer's "10 tests pass" (G5). It is this turn's only, since an earlier turn's green run
        # says nothing about what changed since.
        from harness.loop.verification import as_row, last_verification

        start = next(
            (
                index
                for index in range(len(self.messages) - 1, -1, -1)
                if is_user_turn(self.messages[index]) and not self.messages[index].get("_steer")
            ),
            0,
        )
        check = as_row(last_verification(self.messages[start:]))
        if check is not None:
            receipt["check"] = check
        # The files that are the agent's own notes (its memory folders). A surface counts the
        # person's files apart from them: memory saves are said quietly, never as "files changed"
        # beside the code (owner, 2026-10-13).
        notes = self._notes_among([*receipt.get("created", []), *receipt.get("overwritten", [])])
        if notes:
            receipt["notes"] = notes
        # Saved too, as a display-only notice: the live frame alone left a reopened session with no
        # receipt at all (GUI review). Every turn ending calls this once.
        self._append_notice("changes", changes=receipt)
        return {"changes": receipt}
