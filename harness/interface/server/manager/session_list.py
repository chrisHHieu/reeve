"""SessionListMixin: the sidebar's rows."""

from __future__ import annotations

from typing import Any, Optional

from harness.modes.agents import resolve_agent_id
from harness.session.sessions import SessionRecord

from .session_work import SessionWork

#: How much of the oldest waiting prompt the row carries: the queue's line under the title.
ATTENTION_TITLE_MAX = 120


class SessionListMixin:
    def _work(self) -> SessionWork:
        """The queue's caches (session_work.py), made on first use."""
        work = getattr(self, "_session_work", None)
        if work is None:
            work = self._session_work = SessionWork(self._git, self.session_store.transcript_path)
        return work

    def list_sessions(self, workspace: Optional[str] = None, *, include_children: bool = False) -> list[dict[str, Any]]:
        """The sidebar's session list. By default only roots: a subagent's child session is real
        (the task tool resumes it and the GUI opens it) but is never a sidebar row, while a
        FORK's `kind` is "root" and so stays where it was. `include_children=True` is the
        explicit way to see the raw set."""
        folder = self.resolve_workspace(workspace) if workspace else None
        # The bound environments are resolved ONCE for the whole list, so a remote session's row
        # carries the reduced profile shape and the sidebar can badge it without a request per
        # row. The shape never includes the identity file.
        environments: dict[str, dict[str, Any]] = {}
        store = self.remote_environments
        if store is not None:
            try:
                for profile in store.list():
                    environments[str(profile.get("id"))] = self.environment_public(profile)
            except Exception:
                environments = {}
        rows = []
        for record in self.session_store.list(workspace=folder, include_children=include_children):
            if not record.session_id.startswith("__"):
                rows.append(self._session_row(record, environments))
                continue
            # Internal threads are hidden, with one exception: the conversation of an
            # automation's run while it waits on the person. Nobody has it open, so the list is
            # the only place its prompt can be found from.
            waiting = self._waiting_run_row(record, environments)
            if waiting is not None:
                rows.append(waiting)
        return rows

    def _waiting_run_row(self, record: SessionRecord, environments: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
        """The row of a run's conversation that holds a prompt for the person, named after its
        automation; None for any other internal thread, and for a run that waits on nothing."""
        if not self.inbox.pending(session_id=record.session_id):
            return None
        task = self.task_store.task_for_run_session(record.session_id)
        if task is None:
            return None
        row = self._session_row(record, environments)
        row["title"] = task.title
        row["automation"] = {"id": task.id, "title": task.title}
        return row

    def _session_row(self, record: SessionRecord, environments: dict[str, dict[str, Any]]) -> dict[str, Any]:
        environment_id = getattr(record, "environment_id", None) or None
        pending = self.inbox.pending(session_id=record.session_id)
        return {
            "session_id": record.session_id,
            "title": record.title or "New session",
            "workspace": record.workspace,
            # The absolute POSIX path on the bound host. A surface builds a remote readout from
            # THIS and never from `workspace`, which is the session's local anchor: a stale local
            # spelling must not be appended to `user@host:port`. Null for a local session.
            "remote_workspace": (record.workspace or None) if environment_id else None,
            # The per-conversation temporary folder of a plain chat: the sidebar groups such a
            # session with the chats that have no folder, never under its generated name.
            "temp_workspace": (not environment_id) and self.is_temp_workspace(record.workspace),
            # Null for a local session, else the profile id whose host owns `workspace`. A
            # surface that does not read the full row still sees it.
            "environment_id": environment_id,
            "environment": environments.get(environment_id or ""),
            # A retired persona id (`code`) is resolved HERE, at the read boundary, so
            # an old session keeps its sidebar home and loads the merged toolset without its
            # stored row being rewritten.
            "agent": resolve_agent_id(record.agent),
            "model": record.model,
            "mode": record.mode,
            "updated_at": record.updated_at,
            "messages": record.message_count,
            "pinned": record.pinned,
            "archived": record.archived,
            # A non-user origin ("slack") and its label drive the "From Slack" group and the
            # row's platform icon.
            "origin": record.origin,
            "origin_label": record.origin_label,
            # Attention is how many of this session's prompts wait on the person, with the kind
            # of the oldest (an approval, a question, …): a surface lists the session under what
            # needs the person. Liveness is working (a turn in flight), sleeping (a self-wake
            # pending) or idle, a dot without a count.
            "attention": len(pending),
            "attention_kind": pending[0].kind if pending else None,
            # What the oldest prompt asks, so the queue can say it under the title.
            "attention_title": (pending[0].title or "")[:ATTENTION_TITLE_MAX] if pending else None,
            "liveness": self._session_liveness(record.session_id),
            # While sleeping, the next timer fire (ISO) drives the "sleeping until…" strip, so a
            # scheduled agent never reads as a dead one.
            "sleeping_until": self._sleeping_until(record.session_id),
            # The channels this session listens to drive the per-session connections indicator.
            "subscriptions": [sub.channel for sub in self.subscriptions.for_session(record.session_id)],
            # The work queue's facts: the folder's branch and whether it is a worktree,
            # and what the session changed, with how much of it git still lists as uncommitted.
            # Best-effort: a failure costs the facts, never the row.
            **self._work_fields(record, environment_id),
        }

    def _work_fields(self, record: SessionRecord, environment_id: Optional[str]) -> dict[str, Any]:
        try:
            return self._work().fields(
                record.session_id, record.workspace, record.updated_at, local=not environment_id
            )
        except Exception:
            return {}

    def _sleeping_until(self, session_id: str) -> Optional[str]:
        fires = [wake.fire_at for wake in self.wakes.pending(session_id) if wake.kind == "timer" and wake.fire_at]
        return min(fires) if fires else None

    def _session_liveness(self, session_id: str) -> str:
        if self.is_running(session_id):
            return "working"
        return "sleeping" if self.wakes.pending(session_id) else "idle"
