"""UndoMixin: taking back the effect of one tool call, from the session's undo store."""

from __future__ import annotations

from typing import Any


class UndoMixin:
    def _undo_store(self, session_id: str):
        engine = self._engines.get(session_id)
        return getattr(engine, "undo", None) if engine else None

    def undoable_calls(self, session_id: str) -> dict[str, Any]:
        """The calls this session can still take back. A surface that rebuilt its rows from the
        transcript cannot know otherwise, since the transcript records what a call did and not
        whether its before-image survived. Read only."""
        store = self._undo_store(session_id)
        return {"call_ids": store.undoable_calls() if store is not None else []}

    def undo_tool_call(self, session_id: str, call_id: str) -> dict[str, Any]:
        """Restore the before-images one tool call captured.

        This is deliberately not written back into the conversation. The files return to what
        they were, and the transcript keeps saying the agent wrote them, because it did.
        Rewriting history to match would be a second and larger claim, and the model's next turn
        reads the files again anyway, so it sees the truth without being told.
        """
        store = self._undo_store(session_id)
        if store is None:
            return {"ok": False, "error": "this session has nothing to undo"}
        if not call_id:
            return {"ok": False, "error": "no step named"}
        return store.restore(call_id)
