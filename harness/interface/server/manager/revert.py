"""RevertMixin — message-level undo/redo over the canonical transcript.

`/undo` stages a marker naming the last user message the model may still see; `/redo`
walks the boundary forward or clears it. Nothing is deleted: the stored transcript is
the record, and the marker is a projection (harness/session/revert.py) applied by the engine's
outbound assembly. This module owns the session-level verbs; the engine owns the view.

The behaviour:

- `/undo` interrupts a running turn first ( then
  `revert.stage`), so the boundary is staged over a settling transcript.
- The reverted user message is returned to the caller so the composer can pull it back
  into the draft.
- `/redo` refuses while a
  turn is running.

Deliberate: a destructive design would DELETE the reverted messages when the
next prompt arrives. Ours cannot: the
transcript is the record, so the marker keeps `staged_at` and the engine sends the
prefix plus whatever was appended after the undo (harness/session/revert.py).
"""

from __future__ import annotations

import time
from harness.session.sessions import SessionRecord
from typing import Any, Optional


from harness.session import revert as _revert
from harness.llm.attachments import content_to_text
from harness.session.turns import is_user_turn


def _revert_payload(message: dict[str, Any], index: int) -> dict[str, Any]:
    """The reverted user message as the composer can take it back.

    `_display` wins over `content` for the same reason the transcript bubble does: a
    force-run `/skill …` line carries model-facing framing in `content` and the person's
    literal line in `_display`. Image parts come back as attachments; PDF/text parts are
    not restored in v1 (they can be large, and the file is still on disk to re-attach).
    """
    content = message.get("content")
    display = message.get("_display")
    text = (
        display
        if isinstance(display, str) and display
        else content_to_text(content, image_placeholder="")
    )
    attachments: list[dict[str, Any]] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            url = (part.get("image_url") or {}).get("url")
            if isinstance(url, str) and url.startswith("data:image/"):
                attachments.append({"kind": "image", "name": "image", "data_url": url})
    return {"index": index, "text": text, "attachments": attachments}


class RevertMixin:
    def _revert_live_messages(
        self, session_id: str, record: SessionRecord
    ) -> list[dict[str, Any]]:
        """The transcript the boundary indexes — the live engine's thread when there is
        one (mid-turn it is ahead of the persisted record), else the record's messages."""
        engine = self._engines.get(session_id)
        if engine is not None:
            return list(engine.messages)
        return list(record.messages)

    def _set_revert(self, session_id: str, marker: Optional[dict[str, Any]]) -> None:
        """Persist the marker and mirror it onto a live engine in one place, so the
        durable record and the outbound view can never disagree."""
        self.session_store.set_revert(session_id, marker or {})
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.revert_state = dict(marker) if marker else None
            # A staged marker rewrites the outbound prefix, so the provider's cache is
            # unusable for the next request — the free moment for the opportunistic
            # prune (`engine/compaction._opportunistic_prune`).
            engine.mark_prefix_dirty()

    def revert_marker(self, session_id: str) -> Optional[dict[str, Any]]:
        """The session's current revert marker, None when none. Read by the messages route
        so a reload draws the same boundary the live session had."""
        record = self.session_store.load(session_id)
        marker = dict(record.revert or {}) if record is not None else {}
        return marker or None

    def revert_session(
        self, session_id: str, *, message_id: Optional[Any] = None
    ) -> dict[str, Any]:
        """Stage a revert at a user message (the last one by default).

        Interrupts a running turn first, then writes the marker.
        The marker is additive: the transcript is untouched and the engine truncates the
        outbound view at `boundary` (harness/session/revert.py). Repeating `/undo` walks the
        boundary back one user message at a time; when no visible user message is left,
        it refuses rather than hiding everything.
        """
        record = self.session_store.load(session_id)
        if record is None:
            return {"ok": False, "error": "this session is not known"}
        if self.is_running(session_id):
            self.cancel_session(session_id)
        messages = self._revert_live_messages(session_id, record)
        user_indexes = [
            index for index, message in enumerate(messages) if is_user_turn(message)
        ]
        if not user_indexes:
            return {"ok": False, "error": "there is nothing to undo yet — no user messages"}
        marker = record.revert or {}
        boundary = _revert.marker_boundary(marker)
        if message_id is not None and str(message_id).strip():
            target = self._resolve_revert_target(message_id, user_indexes)
            if target is None:
                return {"ok": False, "error": f"no user message at {message_id!r}"}
        elif boundary is None:
            target = user_indexes[-1]
        else:
            candidates = [index for index in user_indexes if index < boundary]
            if not candidates:
                return {"ok": False, "error": "there is nothing left to undo"}
            target = candidates[-1]
        new_marker = {
            "boundary": target,
            "staged_at": len(messages),
            "ts": time.time(),
        }
        self._set_revert(session_id, new_marker)
        # Bump the session's revert generation: every background
        # child born before this point belongs to the span the person just hid, and its
        # completion must not be delivered into the visible tail
        # (`session_roots._inject_background_result`).
        self._revert_generation[session_id] = (
            self._revert_generation.get(session_id, 0) + 1
        )
        return {
            "ok": True,
            "session_id": session_id,
            "revert": new_marker,
            "message": _revert_payload(messages[target], target),
        }

    @staticmethod
    def _resolve_revert_target(
        message_id: Any, user_indexes: list[int]
    ) -> Optional[int]:
        """A caller-named boundary. Our messages carry no ids, so the wire name is the
        message's canonical INDEX (an int or its string form); it must name a user turn."""
        try:
            index = int(str(message_id).strip())
        except (TypeError, ValueError):
            return None
        return index if index in user_indexes else None

    def unrevert_session(self, session_id: str) -> dict[str, Any]:
        """Redo: walk the boundary forward to the next user message, or clear it.

        `/redo`: the next user message
        after the boundary is re-staged at, and when there is none the marker is cleared.
        Refused mid-turn, like the v1 service's `assertNotBusy`.
        """
        record = self.session_store.load(session_id)
        if record is None:
            return {"ok": False, "error": "this session is not known"}
        marker = record.revert or {}
        boundary = _revert.marker_boundary(marker)
        if boundary is None:
            return {"ok": False, "error": "there is nothing to redo"}
        if self.is_running(session_id):
            return {
                "ok": False,
                "error": "the session is mid-turn — redo once the turn settles",
            }
        messages = self._revert_live_messages(session_id, record)
        following = [
            index
            for index, message in enumerate(messages)
            if is_user_turn(message) and index > boundary
        ]
        if not following:
            self._set_revert(session_id, None)
            return {"ok": True, "session_id": session_id, "revert": None, "cleared": True}
        new_marker = {
            "boundary": following[0],
            "staged_at": len(messages),
            "ts": time.time(),
        }
        self._set_revert(session_id, new_marker)
        return {
            "ok": True,
            "session_id": session_id,
            "revert": new_marker,
            "cleared": False,
            "message": _revert_payload(messages[following[0]], following[0]),
        }
