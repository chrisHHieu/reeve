"""The four tools that let the agent manage automations: create, list, update, delete.

All but listing wait for approval, so no standing automation exists without the person having seen
its card. The model writes the cron itself from "every weekday at 7:10pm". A task remembers the
conversation that made it and works in that conversation's folder, so what it produces is files the
conversation can open.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from harness.tools.declare import declare_tool

from . import tool_schemas
from .models import Schedule, ScheduledTask, grant_entries
from .store import TaskStore


def _as_tool(func: Callable, schema: dict, *, asks: bool) -> Callable:
    return declare_tool(func, schema, category="automation", capabilities=["scheduling"], risk="medium" if asks else "low", asks=asks)


def _cron_problem(cron: Optional[str]) -> Optional[dict]:
    from croniter import croniter

    if cron is not None and not croniter.is_valid(cron):
        return {"error": f"invalid cron expression: {cron}"}
    return None


def scheduling_tools(store: TaskStore, *, origin: dict[str, Any], default_workspace: str) -> list[Callable[..., Any]]:
    def create_scheduled_task(title, instructions, cron=None, fire_at=None, timezone="local", permissions=None):
        if not (cron or fire_at):
            return {"error": "give a cron for a repeating task or fire_at (an ISO datetime) for a single run"}
        if cron and (problem := _cron_problem(cron)):
            return problem
        schedule = Schedule(kind="cron" if cron else "once", cron=cron, fire_at=fire_at, timezone=timezone or "local")
        workspace = origin.get("workspace") or default_workspace
        # What the model proposes, the person grants by approving this call: the card shows the
        # proposal. `grant_entries` keeps only a valid write grant, one on a tool that declares a
        # target argument (never a shell or destructive tool) with that target filled in.
        grants = grant_entries(permissions)
        task = store.save(
            ScheduledTask(
                title=title,
                instructions=instructions,
                schedule=schedule,
                workspace=workspace,
                origin_surface=origin.get("surface", "app"),
                origin_session_id=origin.get("session_id", ""),
                agent=origin.get("agent", "reeve"),
                always_allowed_tools=grants,
            )
        )
        return {
            "ok": True,
            "id": task.id,
            "title": title,
            "schedule": schedule.human(),
            "next_run": task.next_run,
            "workspace": workspace,
            "always_allowed": grants,
        }

    def list_scheduled_tasks():
        return {"tasks": [task.public() for task in store.list()]}

    def update_scheduled_task(id, enabled=None, instructions=None, cron=None, title=None):
        task = store.get(id)
        if task is None:
            return {"error": f"there is no automation with id {id}"}
        if problem := _cron_problem(cron):
            return problem
        if cron is not None:
            task.schedule.kind, task.schedule.cron = "cron", cron
        if enabled is not None:
            task.enabled = bool(enabled)
        task.instructions = task.instructions if instructions is None else instructions
        task.title = task.title if title is None else title
        store.save(task)
        return {"ok": True, "task": task.public()}

    def delete_scheduled_task(id):
        return {"ok": store.delete(id), "id": id}

    return [
        _as_tool(create_scheduled_task, tool_schemas.CREATE, asks=True),
        _as_tool(list_scheduled_tasks, tool_schemas.LIST, asks=False),
        _as_tool(update_scheduled_task, tool_schemas.UPDATE, asks=True),
        _as_tool(delete_scheduled_task, tool_schemas.DELETE, asks=True),
    ]
