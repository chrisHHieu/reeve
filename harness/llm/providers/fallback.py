"""Model fallback chain + per-route cooldown.

A session has one route. The retry taxonomy in `errors.py` re-sends the same request to
the SAME model, which is right for a flaky connection and useless when the route itself
is the problem: an out-of-quota account gets zero retries (correctly) and the turn dies,
a rate-limited route burns its four attempts and then dies, and a provider asking for a
long `Retry-After` ends the turn with the number said aloud. With a configured chain the
engine cools the failed route and re-sends to the next one before it gives up.

What lives here: eligibility (which failures are a ROUTE problem), the cooldown table
(per-route strikes, escalating 60s·2ⁿ capped at 4h, reset on a successful stream), and
the chain walk. The engine owns the switch itself (`switch_model` already exists) and the
observability rows.

Deliberately not here: content-policy fallback (a different censorship answer is a policy
choice, not a reliability one), unknown-error fallback (the same conservative default the
retry classifier uses), and persisted cooldowns (a fresh process should re-probe — quota
may have been topped up — and a persisted 4h block would outlive the fix).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional

from .errors import classify_stream_error, retry_after_seconds

#: The classes that mean "this route is down", not "this request is bad".
FALLBACK_CLASSES = frozenset(
    {"rate_limit", "overloaded", "no_quota", "no_access", "transport", "empty", "routing"}
)

#: Escalation: 60s, 2m, 4m … capped at 4h. A route that fails once is
#: skipped for a minute; one that keeps failing backs off to a quarter of a day.
COOLDOWN_BASE_SECONDS = 60.0
COOLDOWN_MAX_SECONDS = 4 * 3600.0
#: `no_quota`/`no_access` are about the ACCOUNT, not the minute: re-probing in 60s only
#: buys the same rejection. Pin them at the cap.
COOLDOWN_PERMANENT_SECONDS = COOLDOWN_MAX_SECONDS
#: transport/empty are re-probed sooner — a dead connection or one bad upstream serving
#: an empty stream says nothing about the next request.
COOLDOWN_BRIEF_SECONDS = 30.0


def fallback_eligible(exc: BaseException) -> bool:
    """True when another route has a real chance of answering this failure."""
    return classify_stream_error(exc) in FALLBACK_CLASSES


def cooldown_seconds(exc: BaseException) -> Optional[float]:
    """How long the failed route should be skipped, or None for "do not cool".

    The provider's own `Retry-After` wins for the throttle classes: it is the only
    number that knows when the route comes back. The caller escalates with strikes when
    the header is absent (passing None to `RouteCooldown.cool`).
    """
    kind = classify_stream_error(exc)
    if kind not in FALLBACK_CLASSES:
        return None
    if kind in {"no_quota", "no_access"}:
        return COOLDOWN_PERMANENT_SECONDS
    if kind in {"transport", "empty"}:
        return COOLDOWN_BRIEF_SECONDS
    return retry_after_seconds(exc)  # None → the caller's strike ladder


class RouteCooldown:
    """Per-route cooldown, in-process, clock-injectable.

    One instance is shared by every engine in the process (a dead provider is dead for
    all sessions), which is also why tests construct their own: a shared table would leak
    a scripted failure into the next test.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._lock = threading.Lock()

    def cool(self, route: str, seconds: Optional[float] = None) -> float:
        """Cool *route* and return how long. None → the next rung of 60s·2ⁿ (capped)."""
        with self._lock:
            strikes = self._strikes.get(route, 0)
            if seconds is None:
                seconds = min(
                    COOLDOWN_BASE_SECONDS * (2**strikes), COOLDOWN_MAX_SECONDS
                )
            else:
                seconds = min(max(float(seconds), 0.0), COOLDOWN_MAX_SECONDS)
            self._strikes[route] = strikes + 1
            self._until[route] = self._clock() + seconds
            return seconds

    def remaining(self, route: str) -> float:
        """Seconds left in *route*'s cooldown (0 when it is usable)."""
        with self._lock:
            until = self._until.get(route)
            if until is None:
                return 0.0
            left = until - self._clock()
            if left <= 0:
                self._until.pop(route, None)
                return 0.0
            return left

    def reset(self, route: str) -> None:
        """A successful stream clears the route's strikes and cooldown."""
        with self._lock:
            self._until.pop(route, None)
            self._strikes.pop(route, None)

    def clear(self) -> None:
        with self._lock:
            self._until.clear()
            self._strikes.clear()


_DEFAULT = RouteCooldown()


def default_cooldown() -> RouteCooldown:
    """The process-wide table every engine shares unless a caller injects its own."""
    return _DEFAULT


def next_route(
    current: str, chain: Iterable[str], cooldown: RouteCooldown
) -> Optional[str]:
    """The first usable route in *chain* that is not the one that just failed.

    Returns None when every candidate is the current route or is cooling — the caller
    then lands in the ordinary error path. Nothing here waits out a cooldown: a turn
    never sits still for a route to come back.
    """
    for route in chain:
        if not route or route == current:
            continue
        if cooldown.remaining(route) <= 0:
            return route
    return None
