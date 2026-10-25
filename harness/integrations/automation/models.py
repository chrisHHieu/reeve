"""What an automation is on disk: the task (its instructions and when it fires) and each run of it.

A task owns one thread (`__task__<id>`) and works in one folder; every firing is a new run, with a
conversation of its own (`__run__<run_id>`) that can be opened and continued afterwards. The field
names are the stored JSON's keys, so renaming one strands every task already saved.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .rules import grant_entries, rule_entry, rule_parts
from .schedule import Schedule

__all__ = ["Schedule", "ScheduledTask", "TaskRun", "grant_entries", "rule_entry", "rule_parts"]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass
class ScheduledTask:
    title: str
    instructions: str
    schedule: Schedule
    workspace: str
    origin_surface: str = "app"  # the surface it was made from
    origin_session_id: str = ""
    agent: str = "reeve"
    id: str = field(default_factory=lambda: _new_id("task"))
    task_session_id: str = ""  # filled in as `__task__<id>`
    model: Optional[str] = None
    # A permission preset as the composer names it (read-only, workspace-write,
    # danger-full-access; older names still resolve through Mode). None runs unattended as
    # workspace-write, with anything that would ask parked for the person.
    mode: Optional[str] = None
    notify_on_completion: bool = True
    notify_target: Optional[str] = None  # an extra place to tell, e.g. "telegram:123"
    always_allowed_tools: list[str] = field(default_factory=list)
    always_allowed_commands: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    next_run: Optional[float] = None  # epoch seconds; the store works it out on save
    last_run: Optional[float] = None
    last_status: Optional[str] = None
    run_count: int = 0
    max_runs: Optional[int] = None
    # The sidebar counts runs that started after this as new; opening the automation moves it to
    # now. 0.0: never opened.
    seen_runs_at: float = 0.0

    def __post_init__(self) -> None:
        self.task_session_id = self.task_session_id or f"__task__{self.id}"

    def to_dict(self) -> dict:
        stored = dict(vars(self))
        stored["schedule"] = self.schedule.to_dict()
        return stored

    @classmethod
    def from_dict(cls, stored: dict) -> "ScheduledTask":
        return cls(**{**stored, "schedule": Schedule.from_dict(stored.get("schedule") or {})})

    # -- what it may do without asking ------------------------------------------------------

    def _rules(self) -> list[tuple[str, Optional[str]]]:
        return [rule_parts(entry) for entry in self.always_allowed_tools]

    def standing_rules(self) -> dict[str, set[str]]:
        """Grants bound to a target, as `{tool: {target, …}}`: what the permission engine checks a
        call's declared target argument against."""
        bound: dict[str, set[str]] = {}
        for tool, target in self._rules():
            if tool and target:
                bound.setdefault(tool, set()).add(target)
        return bound

    def name_allowed_tools(self) -> set[str]:
        """Grants from before targets: a tool's name and nothing else."""
        return {tool for tool, target in self._rules() if tool and target is None}

    def add_rule(self, tool: str, target: str) -> bool:
        """Grant `tool` on `target`; False when either is empty or the grant is already there."""
        entry = rule_entry(tool, target)
        if tool and target and entry not in self.always_allowed_tools:
            self.always_allowed_tools.append(entry)
            return True
        return False

    def revoke_rule(self, entry: str) -> bool:
        if entry in self.always_allowed_tools:
            self.always_allowed_tools.remove(entry)
            return True
        return False

    def public(self) -> dict[str, Any]:
        """The task as the API hands it to the app: the whole instructions, no secrets."""
        shown = {
            name: getattr(self, name)
            for name in (
                "id",
                "title",
                "instructions",
                "workspace",
                "agent",
                "model",
                "mode",
                "enabled",
                "next_run",
                "last_run",
                "last_status",
                "run_count",
                "notify_on_completion",
                # The detail page keeps the mark from before it opened, to say which runs are new.
                "seen_runs_at",
            )
        }
        shown["schedule"] = self.schedule.human()
        shown["schedule_raw"] = self.schedule.to_dict()
        # The grants the task page lists for revoking; `entry` is what a revoke sends back.
        shown["always_allowed"] = [
            {"entry": entry, "tool": tool, "target": target}
            for entry in sorted(set(self.always_allowed_tools))
            for tool, target in [rule_parts(entry)]
        ]
        return shown


@dataclass
class TaskRun:
    task_id: str
    run_id: str = field(default_factory=lambda: _new_id("run"))
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    status: str = "running"  # then ok, error or skipped
    result_text: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)
    error: Optional[str] = None
    trigger: str = "schedule"  # or manual, or catchup (missed while the server was down)
    session_id: str = ""  # filled in as `__run__<run_id>`

    def __post_init__(self) -> None:
        self.session_id = self.session_id or f"__run__{self.run_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, stored: dict) -> "TaskRun":
        return cls(**stored)
