"""When a task fires next."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .models import ScheduledTask


def epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def zone_of(name: str):
    """The tzinfo for a schedule's timezone, or None for the machine's own zone.

    None is deliberate and a fixed-offset tzinfo would be wrong: with a naive datetime, `.timestamp()`
    lets the C library apply local daylight saving at the date it fires. An offset read when the
    schedule was computed carried that moment's offset and misfired across a change. An unknown name
    falls back to local rather than raising."""
    if not name or name.lower() == "local":
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def compute_next_run(task: ScheduledTask, *, after: Optional[float] = None) -> Optional[float]:
    """The next fire time in epoch seconds, or None when the task has no more to run."""
    schedule = task.schedule
    now = after if after is not None else epoch_now()
    zone = zone_of(schedule.timezone)
    if schedule.kind == "once":
        if not schedule.fire_at:
            return None
        try:
            moment = datetime.fromisoformat(schedule.fire_at)
        except ValueError:
            return None
        if moment.tzinfo is None and zone is not None:
            moment = moment.replace(tzinfo=zone)
        # A naive local time is read in the machine's zone with the offset of the day it fires, so
        # a "once" set in summer for a winter date lands on the right wall-clock hour.
        fires = moment.timestamp()
        return fires if task.run_count == 0 and fires > now else None

    from croniter import croniter

    if not schedule.cron or not croniter.is_valid(schedule.cron):
        return None
    if task.max_runs is not None and task.run_count >= task.max_runs:
        return None
    # A naive base makes croniter count in local wall-clock time and gives each occurrence its own
    # daylight-saving offset; a named zone anchors the base in that zone.
    base = datetime.fromtimestamp(now) if zone is None else datetime.fromtimestamp(now, tz=zone)
    return croniter(schedule.cron, base).get_next(datetime).timestamp()
