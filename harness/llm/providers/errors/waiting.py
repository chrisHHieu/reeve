"""A provider's own `Retry-After`, and what the loop does with it."""

from __future__ import annotations

from typing import Optional

#: How long the loop sits still for a provider's `Retry-After`. Under it the number is a wait worth
#: taking (a 429 answered early is another 429); over it, the honest move is to end the turn with the
#: number said out loud and not to retry early (the engine loop's `abandoned` path).
RETRY_AFTER_MAX_WAIT_SECONDS = 60.0

#: Beyond this the header is not a wait at all (a broken proxy, a mis-set clock) and is ignored as
#: if it had not been sent. It sits far above the wait ceiling so that "the provider asked for longer
#: than we will wait" and "nobody sent a usable number" stay distinguishable.
_SANITY_SECONDS = 3600.0


def retry_after_seconds(exc: BaseException, *, max_seconds: float = _SANITY_SECONDS) -> Optional[float]:
    """The provider's `Retry-After` in seconds, or None when none was sent or it is unusable.

    A 429 answered sooner than the provider asked is another 429, so its number beats any local
    backoff. A long value is returned as it is: the caller decides whether to wait it out or to give
    the wait as the failure's explanation. An earlier version swallowed a 300 here, which turned
    "come back in five minutes" into a ten-second retry that was bound to fail and spent the retry
    budget finding out.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
        seconds = float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None
    if seconds is None or seconds < 0 or seconds > max_seconds:
        return None
    return seconds
