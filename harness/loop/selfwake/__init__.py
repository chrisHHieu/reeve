"""Self-wake: tools that let a long-running agent suspend and be started again on a trigger.

It turns an always-on agent into suspend and resume, event-driven and at about zero cost while idle:
the session sleeps and the runtime starts it again when a wake is due. Two triggers are here, a timer
(`sleep_until`) and the completion of a backgrounded job (`wake_on`), and a third, a named event.
`records` holds the wake and its limits, `store` keeps them and decides which requests may become
one, and the scheduler tick consumes `due()` and `complete_job()` and resumes the session (it shares
the automation scheduler; see `PERMISSIONS-AND-INBOX.md`).
"""

from __future__ import annotations

from .records import (
    KIND_COMPLETION,
    KIND_EVENT,
    KIND_TIMER,
    MAX_PENDING_WAKES_PER_SESSION,
    MAX_WAKE_DEPTH,
    STATE_DUE,
    STATE_FIRED,
    STATE_PENDING,
    WAKE_TURNS_PER_HOUR,
    Wake,
    WakeRefused,
)
from .store import WakeStore
from .tools import selfwake_tools

__all__ = [
    "KIND_COMPLETION", "KIND_EVENT", "KIND_TIMER", "MAX_PENDING_WAKES_PER_SESSION", "MAX_WAKE_DEPTH",
    "STATE_DUE", "STATE_FIRED", "STATE_PENDING", "WAKE_TURNS_PER_HOUR", "Wake", "WakeRefused",
    "WakeStore", "selfwake_tools",
]
