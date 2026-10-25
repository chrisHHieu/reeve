"""ScheduledRunsMixin: running a scheduled task as a real conversation, and telling people."""

from __future__ import annotations

import asyncio
import time

from harness.integrations.automation import TaskRun

from .helpers import last_assistant_text, recent_files

_SUMMARY_CHARS = 280


class ScheduledRunsMixin:
    async def _run_scheduled_task(self, task, trigger: str) -> TaskRun:
        run = TaskRun(task_id=task.id, trigger=trigger)  # `__post_init__` sets the session id
        self.task_store.add_run(run)  # marks it "running"
        # Every open app window is told that a SCHEDULED run just started, for the toast. A
        # manual run never comes through here, because the person is already watching it live.
        await self.broadcast_event(
            {
                "type": "automation_run_started",
                "data": {
                    "task_id": task.id,
                    "task_title": task.title,
                    "session_id": run.session_id,
                    "workspace": task.workspace,
                    "agent": task.agent,
                    "trigger": trigger,
                },
            }
        )
        # Each run is a real, persisted conversation under its own session id, so the person can
        # reopen it and ask a follow-up. The scheduled agent is not fire-and-forget.
        engine = self._build_task_engine(task, session_id=run.session_id)
        # The live engine is registered up front: a parked approval saves the session mid-run
        # (a durable suspend), and resolving it from the Inbox has to find this engine.
        self._engines[run.session_id] = engine
        # Only then is MCP armed, so it attaches to THIS engine. Nobody watches a scheduled run,
        # which is where missing tools are least visible, so it gets the tool set an attended
        # session would and not a quietly smaller one.
        await self.arm_mcp(run.session_id, workspace=task.workspace, agent=task.agent)
        # The first turn is the task itself. Its instructions often restate the schedule ("every
        # day at 5:32pm…"), so the framing says the schedule has already fired and the job is to
        # execute and not to schedule again.
        opening = (
            f"⏰ Scheduled run — {task.title}\n\n"
            "This automation is due now: carry out the task below immediately and produce the "
            "result. The schedule already exists — do not create or modify any scheduled tasks.\n\n"
            f"{task.instructions}"
        )
        try:
            # The run is the session's own turn and not a person typing, so it names itself and
            # the `goal` tool does not take it for the person's say-so.
            async for _event in engine.run(opening, source={"kind": "scheduled_run", "task_id": str(task.id)}):
                pass
            run.result_text = last_assistant_text(engine.messages)
            run.artifacts = recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            if task.notify_on_completion:
                await self._notify_task_done(task, run)
        except Exception as exc:
            run.status, run.error = "error", str(exc)
        finally:
            run.finished_at = time.time()
            # The run is saved as a session that can be continued, and the live engine is kept
            # for an immediate follow-up. The run record then carries its session id.
            try:
                self.save(run.session_id, engine)
                self._engines[run.session_id] = engine
            except Exception:
                pass
            self.task_store.add_run(run)
        return run

    async def _notify_task_done(self, task, run: TaskRun) -> None:
        summary = (run.result_text or "").strip()[:_SUMMARY_CHARS]
        # Any socket viewing the run's session is told, since it is a durable session of its own.
        await self.broadcast_session(
            run.session_id,
            {
                "type": "task_done",
                "data": {"task": task.title, "id": task.id, "text": summary, "run_id": run.run_id},
            },
        )
        if not task.notify_target:
            return
        from harness.integrations.connectors.messaging import parse_target
        from harness.integrations.connectors.messaging.senders import DEFAULT_SENDERS

        try:
            platform, chat_id, thread = parse_target(task.notify_target)
            send = DEFAULT_SENDERS.get(platform)
            credentials = self.secrets.get(f"{platform}:default") or {}
            if send and credentials.get("bot_token"):
                await asyncio.to_thread(send, credentials["bot_token"], chat_id, f"✓ {task.title}\n\n{summary}", thread)
        except Exception:
            pass
