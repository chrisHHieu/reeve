"""AutomationApiMixin: what the automation screens read and change."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from harness.integrations.automation import Schedule, ScheduledTask, TaskRun
from harness.safety.permissions import Mode
from harness.session.sessions import SessionRecord

from .helpers import last_assistant_text, recent_files


def _clean(value: Any) -> str:
    return (value or "").strip()


def _mode_error(mode: Optional[str]) -> Optional[dict[str, Any]]:
    """The refusal for a mode name no preset answers to, or None."""
    if mode is None:
        return None
    try:
        Mode(mode)
    except ValueError:
        return {"ok": False, "error": f"invalid mode: {mode}"}
    return None


class AutomationApiMixin:
    def list_automations(self) -> dict[str, Any]:
        # "Unseen" means a run that started after the task's seen mark, which is what the
        # sidebar badges count. `unseen_failed` tints the badge when the NEWEST unseen run erred.
        # `latest_run` is the newest run, so a row can say what the automation last came to
        # (running, done, failed, or waiting on the person) without a request per row.
        tasks = []
        for task in self.task_store.list():
            runs = self.task_store.runs(task.id)
            unseen = [run for run in runs if run.started_at > task.seen_runs_at]
            tasks.append(
                {
                    **task.public(),
                    "unseen_runs": len(unseen),
                    "unseen_failed": bool(unseen) and unseen[0].status == "error",
                    "latest_run": self._run_view(runs[0]) if runs else None,
                }
            )
        return {"tasks": tasks}

    def _run_view(self, run: TaskRun) -> dict[str, Any]:
        """A run as the app reads it: the stored run, with how many prompts of its conversation
        wait on the person and the kind of the oldest. A run with one reads "needs
        you" whatever its status says."""
        pending = self.inbox.pending(session_id=run.session_id)
        return {**run.to_dict(), "attention": len(pending), "attention_kind": pending[0].kind if pending else None}

    def mark_automation_seen(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        task.seen_runs_at = time.time()
        self.task_store.save(task)
        return {"ok": True}

    def get_automation(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"error": "not found"}
        return {"task": task.public(), "runs": [self._run_view(run) for run in self.task_store.runs(task_id)]}

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an automation straight from the GUI (the "New automation" and template flows).

        It validates like the agent-facing `create_scheduled_task`, but binds the task to a fresh
        scratch workspace of its own and not to an originating conversation's folder."""
        from croniter import croniter

        from harness.integrations.automation.models import grant_entries

        title = _clean(payload.get("title"))
        instructions = _clean(payload.get("instructions"))
        cron = _clean(payload.get("cron")) or None
        fire_at = _clean(payload.get("fire_at")) or None
        timezone = _clean(payload.get("timezone")) or "local"

        if not title:
            return {"ok": False, "error": "title is required"}
        if not instructions:
            return {"ok": False, "error": "instructions are required"}
        if not cron and not fire_at:
            return {"ok": False, "error": "provide a cron (recurring) or a fire_at ISO datetime (one-time)"}
        if cron and not croniter.is_valid(cron):
            return {"ok": False, "error": f"invalid cron expression: {cron}"}

        # The editor's control row matches the composer: a project folder, a model and the same
        # permission modes. An empty workspace keeps the managed scratch folder.
        workspace = _clean(payload.get("workspace"))
        model = _clean(payload.get("model")) or None
        mode = _clean(payload.get("mode")) or None
        refused = _mode_error(mode)
        if refused:
            return refused
        task = ScheduledTask(
            title=title,
            instructions=instructions,
            schedule=Schedule(
                kind="once" if (fire_at and not cron) else "cron", cron=cron, fire_at=fire_at, timezone=timezone
            ),
            workspace=workspace,
            model=model,
            mode=mode,
            origin_surface="app",
            agent="reeve",
            # A form a person filled in is consent: the surface rendered the grants and the
            # submit accepts them. The validation is the agent tool's, so only grants bound to a
            # target survive.
            always_allowed_tools=grant_entries(payload.get("permissions")),
        )
        if not workspace:
            task.workspace = self._provision_scratch(task.task_session_id)
        self.task_store.save(task)
        return {"ok": True, "task": task.public()}

    def update_automation(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        if "enabled" in changes:
            task.enabled = bool(changes["enabled"])
        if changes.get("instructions") is not None:
            task.instructions = changes["instructions"]
        if changes.get("title") is not None:
            task.title = changes["title"]
        if changes.get("cron") is not None:
            from croniter import croniter

            if not croniter.is_valid(changes["cron"]):
                return {"ok": False, "error": "invalid cron"}
            task.schedule.cron, task.schedule.kind = changes["cron"], "cron"
        if "model" in changes:
            # An empty value clears back to the server default; the runner falls through to it.
            task.model = _clean(changes.get("model")) or None
        if "mode" in changes:
            mode = _clean(changes.get("mode")) or None
            refused = _mode_error(mode)
            if refused:
                return refused
            task.mode = mode
        if "workspace" in changes:
            task.workspace = _clean(changes.get("workspace"))
            if not task.workspace:
                task.workspace = self._provision_scratch(task.task_session_id)
        if "ask_before_changes" in changes:
            task.ask_before_changes = bool(changes.get("ask_before_changes"))
        revoked = changes.get("revoke")
        if revoked:
            # Revoking comes from the task page ("Allowed without asking … · Revoke"). It is
            # human-only, like minting: the agent-facing update tool has no such field.
            task.revoke_rule(str(revoked))
        self.task_store.save(task)
        if revoked:
            # A live run's engine may still hold the revoked rule, so it is reseeded from the record.
            for session_id, engine in self._engines.items():
                owner = self.task_store.task_for_run_session(session_id)
                if owner is not None and owner.id == task.id:
                    engine.permissions.task_rules = task.standing_rules()
        return {"ok": True, "task": task.public()}

    def delete_automation(self, task_id: str) -> dict[str, Any]:
        return {"ok": self.task_store.delete(task_id), "id": task_id}

    def prepare_manual_run(self, task_id: str) -> dict[str, Any]:
        """Create a 'running' manual run and return its session, so the GUI can open it and
        drive the task live over the ordinary session socket (watching the agent and following
        up). The scheduler's automatic path stays headless (`_run_scheduled_task`)."""
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        run = TaskRun(task_id=task.id, trigger="manual")  # status "running", session id generated
        self.task_store.add_run(run)
        # The session record is written BEFORE the GUI connects. A brand-new session would
        # otherwise build its engine from the SERVER defaults, and a task set to bypass approvals
        # would run asking for them. The record carries the task's mode, model and folder, so the
        # composer reads them too, and the mode goes through `Mode` so a legacy spelling maps
        # onto the three presets.
        self.session_store.save(
            SessionRecord(
                session_id=run.session_id,
                workspace=task.workspace,
                model=task.model or self.model,
                mode=Mode(task.mode or "workspace-write").value,
                title=task.title,
                agent=task.agent,
            )
        )
        return {
            "ok": True,
            "run_id": run.run_id,
            "session_id": run.session_id,
            "workspace": task.workspace,
            "agent": task.agent,
            # The same execute-now framing as the headless path. A manual run rides an ordinary
            # live session whose engine DOES have scheduling tools, so it has to be explicit.
            "prompt": (
                f"⏰ Running automation '{task.title}' now. Carry out these instructions "
                "immediately and produce the result. The schedule already exists — do not create "
                f"or modify any scheduled tasks.\n\n{task.instructions}"
            ),
        }

    def finalize_manual_run(self, task_id: str, run_id: str) -> dict[str, Any]:
        """Mark a manual run complete once its first turn finished (the socket already saved the
        session), taking the result text and artifacts from the stored transcript and workspace."""
        run = next((r for r in self.task_store.runs(task_id) if r.run_id == run_id), None)
        task = self.task_store.get(task_id)
        if run is None or task is None:
            return {"ok": False, "error": "not found"}
        if run.status == "running":
            record = self.session_store.load(run.session_id)
            run.result_text = last_assistant_text(record.messages) if record else None
            run.artifacts = recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            run.finished_at = time.time()
            self.task_store.add_run(run)
            task.last_run, task.last_status = run.finished_at, "ok"
            task.run_count += 1
            self.task_store.save(task)
        return {"ok": True, "run": run.to_dict()}
