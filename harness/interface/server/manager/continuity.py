"""ContinuityMixin: picking up, at startup, the work the last process did not finish.

The store (`harness/session/continuity.py`) says what was running when the process ended: the
sessions whose turn never ended here, and the background commands still alive. This mixin keeps
the store in step while the process runs and reads it once when the next one starts.

A turn whose saved thread ends in an answer did finish (a graceful shutdown let it land, then
kept its claim), so nothing runs. Any other is handed to `engine.pick_up`, which decides from the
thread how to go on: the unanswered calls (never repeating one that had already started,
`tools/intents.py`), the unanswered message, or a continuation message the person sees as a
"Reeve restarted" line (`source.synthetic`).

A background child is picked up the same way and, when it ends, reports to its parent as a
background child always does. A foreground child is not: its parent's turn is the one waiting,
and that turn is picked up instead. A background command cannot be: its process died with the
one that started it, so its session is told, in the continuation when the session has a turn
to pick up, and as a line in its transcript otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from harness.session.continuity import MAX_RESUMES

from .helpers import last_assistant_text

logger = logging.getLogger("harness.manager")

#: The most of a stopped command's output a session is shown or told.
STOPPED_TAIL_CHARS = 1_200

CONTINUE_AFTER_RESTART = (
    "Reeve restarted while you were working on this, and the turn is being picked up. The "
    "conversation above is everything that was done before the restart: continue from where "
    "it stopped, and do not repeat finished steps."
)


def ended_in_answer(messages: list[dict[str, Any]]) -> bool:
    """Whether the thread's last word is the agent's answer: an assistant message with text and
    no calls, after anything that is display-only."""
    for message in reversed(messages or []):
        role = message.get("role")
        if role == "notice":
            continue
        return role == "assistant" and bool(message.get("content")) and not message.get("tool_calls")
    return False


def steps_done(messages: list[dict[str, Any]]) -> int:
    """How many steps the interrupted turn had taken: the assistant messages with calls since the
    last message the person (not the engine) sent."""
    count = 0
    for message in reversed(messages or []):
        role = message.get("role")
        if role == "user" and not (message.get("source") or {}).get("synthetic"):
            break
        if role == "assistant" and message.get("tool_calls"):
            count += 1
    return count


def stopped_jobs_text(jobs: dict[str, dict[str, Any]]) -> str:
    """What the model is told of the background commands the restart stopped."""
    lines = []
    for job in jobs.values():
        tail = str(job.get("tail") or "").strip()[-STOPPED_TAIL_CHARS:]
        line = f"- `{job.get('command') or '?'}` was stopped by the restart"
        lines.append(line + (f"; the last of its output:\n{tail}" if tail else "; none of its output was kept."))
    return "\nBackground commands that were running:\n" + "\n".join(lines) if lines else ""


class ContinuityMixin:
    # -- while the process runs ------------------------------------------------------------

    def _roster_changed(self, session_id: str) -> None:
        """A job joined the roster or left it: the store keeps the running ones, then the roster
        is pushed as before. Called on the executor's threads; the store locks."""
        try:
            rows = self.background_jobs(session_id).get("jobs") or []
            self.continuity.set_jobs(session_id, [row for row in rows if row.get("status") == "running"])
        except Exception:  # noqa: BLE001 - bookkeeping must never cost the job
            logger.debug("continuity: jobs of %s not recorded", session_id, exc_info=True)
        self._jobs_changed(session_id)

    def keep_job_tails(self) -> None:
        """At a graceful shutdown, before the shells are closed: what each running background
        command had printed, so its session can be shown it after the restart."""
        tails: dict[str, dict[str, str]] = {}
        for session_id, engine in list(getattr(self, "_engines", {}).items()):
            tasks = getattr(getattr(engine, "executor", None), "_bg_tasks", None) or {}
            for task_id, task in list(tasks.items()):
                try:
                    if not task.done:
                        tails.setdefault(session_id, {})[task_id] = task.tail(STOPPED_TAIL_CHARS)
                except Exception:  # noqa: BLE001
                    continue
        if tails:
            self.continuity.keep_tails(tails)

    # -- at startup ------------------------------------------------------------------------

    def start_resume_interrupted(self) -> Optional[asyncio.Task]:
        """Schedule the pick-up on the running loop; the server answers while it runs."""
        try:
            task = asyncio.get_running_loop().create_task(self.resume_interrupted_work())
        except RuntimeError:
            return None
        self._resume_task = task
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        return task

    async def resume_interrupted_work(self) -> dict[str, int]:
        """Pick up every turn the last process did not finish, and tell every session whose
        background commands it stopped. Returns counts, for the log and the tests."""
        jobs = self.continuity.take_jobs()
        claims = self.continuity.orphaned_turns()
        counts = {"picked_up": 0, "finished": 0, "given_up": 0, "jobs_told": 0}
        # Children first: a background child's report then reaches a parent that is itself
        # being picked up, as a message its next step reads.
        order = sorted(claims, key=lambda sid: 0 if claims[sid].get("parent") else 1)
        for session_id in order:
            try:
                # `jobs` is shared: a pick-up that tells the model about its stopped commands
                # takes them; what is left is shown as lines below.
                outcome = await self._pick_up(session_id, claims[session_id], jobs)
            except Exception:  # noqa: BLE001 - one session's failure must not stop the others
                logger.warning("continuity: could not pick up %s", session_id, exc_info=True)
                self.continuity.release_turn(session_id)
                continue
            counts[outcome] = counts.get(outcome, 0) + 1
        for session_id, stopped in jobs.items():
            if self._tell_stopped_jobs(session_id, stopped):
                counts["jobs_told"] += 1
        if any(counts.values()):
            logger.info("continuity after restart: %s", counts)
        return counts

    async def _pick_up(
        self, session_id: str, claim: dict[str, Any], all_jobs: dict[str, dict[str, dict[str, Any]]]
    ) -> str:
        record = self.session_store.load(session_id)
        if record is None:
            self.continuity.release_turn(session_id)
            return "finished"
        parent = claim.get("parent")
        if getattr(record, "kind", "root") == "subagent" and not parent:
            # A foreground child: its parent's turn waits on it and is picked up itself.
            self.continuity.release_turn(session_id)
            return "finished"
        attempt = self.continuity.count_resume(session_id)
        engine = self.get_engine(session_id, extra_tools=await self.arm_mcp(session_id))
        if engine is None:
            self.continuity.release_turn(session_id)
            return "finished"
        # The dead process left this turn open in the log: closing it first (synthetically,
        # as any engine adopting such a log does) also gives the engine the turn's number.
        engine._close_dangling_turn()
        if attempt > MAX_RESUMES:
            engine._append_notice(
                "restart",
                f"Reeve restarted {attempt - 1} times during this turn, so it was not picked up again.",
                given_up=True,
            )
            engine.log.append("turn/resumed", turn=engine._turn_no, attempt=attempt, path="none",
                              steps_done=steps_done(engine.messages),
                              jobs_stopped=len(all_jobs.get(session_id) or {}), given_up=True)
            self.save(session_id, engine)
            self.continuity.release_turn(session_id)
            return "given_up"
        pending = engine._unanswered_trailing_tool_calls()
        if not pending and ended_in_answer(engine.messages):
            self.continuity.release_turn(session_id)
            return "finished"
        if not self.try_mark_running(session_id):
            return "finished"  # somebody already started a turn here since the restart
        jobs = all_jobs.pop(session_id, {})
        done = steps_done(engine.messages)
        commands = [str(job.get("command") or "") for job in jobs.values()]
        engine.log.append("turn/resumed", turn=engine._turn_no, attempt=attempt,
                          path="calls" if pending else "continue", steps_done=done, jobs_stopped=len(jobs))
        source = {"synthetic": "restart", "steps": done, **({"jobs": commands} if commands else {})}
        result = await self._drive_turn(
            session_id, engine, "",
            events=engine.pick_up(CONTINUE_AFTER_RESTART, source, stopped_jobs_text(jobs).strip()),
        )
        if parent and self.session_store.load(parent) is not None:
            await self._inject_background_result(
                parent_id=parent,
                child_id=session_id,
                description=getattr(record, "title", "") or "Subagent",
                result={"status": result.status or "completed", "report": last_assistant_text(engine.messages) or ""},
            )
        return "picked_up"

    def _tell_stopped_jobs(self, session_id: str, jobs: dict[str, dict[str, Any]], *, engine: Any = None) -> bool:
        """A session with no turn to pick up gets one line per stopped command in its transcript.
        It is display-only: the model learns it from the running-jobs line of its next turn,
        which no longer lists them, and nothing wakes the session to spend a request on it."""
        if engine is None:
            if self.session_store.load(session_id) is None:
                return False
            engine = self.get_engine(session_id)
        if engine is None:
            return False
        for job in jobs.values():
            tail = str(job.get("tail") or "").strip()[-STOPPED_TAIL_CHARS:]
            engine._append_notice(
                "job_stopped",
                "Background task stopped · Reeve quit while it ran",
                command=str(job.get("command") or ""),
                tail=tail or None,
            )
        self.save(session_id, engine)
        return True
