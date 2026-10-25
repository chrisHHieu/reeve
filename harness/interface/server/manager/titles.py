"""AutoTitleMixin: naming a conversation from its opening, once the first turn is done."""

from __future__ import annotations

import asyncio
import logging

from harness.llm.attachments import content_to_text
from harness.loop import TurnEngine
from harness.session.turns import is_user_turn

logger = logging.getLogger("harness.manager")

_PROMPT = (
    "You title chat sessions. Given the user's opening message(s), reply with ONLY "
    "a 4-5 word title for the session — no quotes or punctuation wrapping it, in "
    "the user's own language. If the opening is merely a greeting or small-talk "
    'with no topic ("hey", "how are you", "hi there"), reply with exactly: small-talk'
)
_MAX_ATTEMPTS = 2
_MAX_STORED = 60
_MAX_ACCEPTED = 80
_QUOTES = "\"'“”‘’`"
_SMALL_TALK = ("small-talk", "smalltalk")


def _clean_title(raw: str) -> str:
    """The model's reply as a title: surrounding quotes off and whitespace collapsed."""
    return " ".join(raw.strip().strip(_QUOTES).split())


def _is_small_talk(title: str) -> bool:
    """Models riff on the sentinel ("Small talk.", quoted, a trailing period), so it is
    normalised before comparing; otherwise the riff would become the title."""
    return title.lower().strip(".!,;:'\"").replace(" ", "-").replace("_", "-") in _SMALL_TALK


class AutoTitleMixin:
    def _maybe_autotitle(self, session_id: str) -> None:
        """Start title generation after a turn completes, without waiting for it.

        It runs only while the session has neither a manual rename nor a generated title, and
        at most twice: the first attempt rides turn 1, and the second window exists solely for
        the small-talk retry, which sees both openers. Attempts are counted in memory and not
        derived from the number of user messages, because a steering injection also lands as a
        user message and counting it would silently suppress titling on a steered first turn.
        A restart that forgets the counter is harmless, since `renamed` and `auto_title` still
        gate re-titling.
        """
        if session_id.startswith("__"):
            return
        if self.is_child_session(session_id):
            # A child is not a workspace conversation, and its title was written when it was
            # spawned ("<description> (@<agent> subagent)").
            return
        engine = self._engines.get(session_id)
        if engine is None or session_id in self._autotitle_inflight:
            return
        if self.task_store.task_for_run_session(session_id) is not None:
            return  # an automation run is titled by its task
        if self._autotitle_attempts.get(session_id, 0) >= _MAX_ATTEMPTS:
            return
        users = [message for message in engine.messages if is_user_turn(message)]
        if not users:
            return
        state = self.session_store.title_state(session_id)
        if state is None or state["renamed"] or state["auto_title"]:
            return
        openers = [
            text
            for message in users
            if (text := content_to_text(message.get("content"), image_placeholder="").strip())
        ][:2]
        if not openers:
            return
        self._autotitle_attempts[session_id] = self._autotitle_attempts.get(session_id, 0) + 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # a synchronous caller has no loop to ride, and must never be blocked
        self._autotitle_inflight.add(session_id)
        # The task is retained: the loop holds only a weak reference, and a collected task would
        # both kill the title mid-flight and strand the in-flight guard.
        task = loop.create_task(self._generate_autotitle(session_id, engine, openers))
        self._autotitle_tasks.add(task)
        task.add_done_callback(self._autotitle_tasks.discard)

    async def _generate_autotitle(self, session_id: str, engine: TurnEngine, openers: list[str]) -> None:
        """One cheap, non-streaming completion on the session's own provider and model. Every
        failure (a provider error, an empty or absurdly long reply) is swallowed and the
        `title_from` fallback stays; the small-talk sentinel leaves `auto_title` unset so the
        second turn's retry can run."""
        try:
            turn = await asyncio.to_thread(
                self._off_turn_provider(engine.provider, "title").complete,
                model=engine.model,
                messages=[
                    {"role": "system", "content": _PROMPT},
                    {"role": "user", "content": "\n\n".join(openers)},
                ],
                temperature=0.2,
                # A thinking model spends hidden tokens BEFORE it emits text, and Gemini bills
                # them against this cap. A 64-token answer survives there only because the
                # provider honours effort "none" as a thinking budget of 0 (the OpenAI-compatible
                # path pins it in `_pin_reasoning_effort`).
                max_tokens=64,
                reasoning_effort="none",
            )
            # A completion cut at the cap is not a title: finish "length" means the budget ended
            # mid-word, and a one-word fragment once survived as the title. Unset beats
            # misleading, since the fallback keeps the seat and the retry window stays open.
            if getattr(turn, "finish_reason", None) == "length":
                return
            title = _clean_title(getattr(turn, "text", None) or "")
            if _is_small_talk(title) or not title or len(title) > _MAX_ACCEPTED:
                return
            # Under the 4-5 words the prompt asks for is a truncation survivor and not a title,
            # which is treated like any other failure.
            if len(title.split()) < 2:
                return
            if self.session_store.set_auto_title(session_id, title[:_MAX_STORED]):
                # A best-effort nudge for any live viewer; the sidebar's poll and the post-turn
                # refresh pick the new title up regardless.
                await self.broadcast_session(
                    session_id,
                    {"type": "session_title", "data": {"session_id": session_id, "title": title[:_MAX_STORED]}},
                )
        except Exception:
            # A failed title must never surface as a session error, and must not be invisible
            # either: a silent provider 400 once hid a token-cap rejection for a whole test pass.
            logger.debug("autotitle failed for %s", session_id, exc_info=True)
        finally:
            self._autotitle_inflight.discard(session_id)
