"""When a task fires: a cron expression or a single moment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Indexed by cron day-of-week, so it starts on Sunday (0 and 7 are Sunday). A Monday-first list
# labelled every weekly schedule one day late.
_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _clock(hour: int, minute: int) -> str:
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


@dataclass
class Schedule:
    kind: str  # "cron" or "once"
    cron: Optional[str] = None
    fire_at: Optional[str] = None  # an ISO datetime, for "once"
    timezone: str = "local"  # "local" is the machine's own clock

    def human(self) -> str:
        """A readable label such as "Every day at ~7:10 PM"; the raw cron when it is not simple."""
        if self.kind == "once":
            return f"Once at {self.fire_at}"
        fields = (self.cron or "").split()
        if len(fields) != 5:
            return self.cron or "?"
        minute, hour, day, _month, weekday = fields
        try:
            at = _clock(int(hour), int(minute))
        except ValueError:
            return self.cron  # ranges and steps are shown as written
        if day == "*" and weekday == "*":
            return f"Every day at ~{at}"
        if day == "*" and weekday.isdigit():
            return f"Every {_WEEKDAYS[int(weekday) % 7]} at ~{at}"
        if day.isdigit() and weekday == "*":
            return f"Monthly on day {day} at ~{at}"
        return self.cron

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cron": self.cron, "fire_at": self.fire_at, "timezone": self.timezone}

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(
            kind=d.get("kind", "cron"),
            cron=d.get("cron"),
            fire_at=d.get("fire_at"),
            timezone=d.get("timezone", "local"),
        )
