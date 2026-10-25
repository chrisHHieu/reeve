"""Noticing when a turn has stopped getting anywhere, and saying so to the model.

The turn loop stops after an agent's step cap; a built-in with
no declared cap runs until the model stops, and the global `Config.max_iterations` is only
a fallback for agents that declare none (0 = no cap). That bound is a backstop against
runaway cost, and it does its job — but as the ONLY answer to a stuck agent it fails
twice. The model is never told it is repeating itself, so it has no reason to try
something else; and when the cap trips the user gets "max iterations reached", which
names the mechanism that stopped it and nothing about what it was stuck on.

The signal used here is deliberately not a call count. Counting alone is wrong in both
directions: `shell_task_output` is *designed* to be polled, and re-reading a file after
an edit is ordinary work, while a single retry of a genuinely broken call is not a loop.
What actually identifies a stuck agent is **the same call producing the same answer** —
identical tool, identical arguments, identical result. That is work with no effect, by
definition, and it needs no per-tool allowlist to recognise: a poll that returns new
output is making progress and is never flagged, and a failing API that returns the same
error every time is flagged on the third try.

Denials count too. A model re-proposing something the permission engine keeps refusing is
the loop a user is most likely to sit and watch.

Nothing is blocked. Repetition is sometimes correct, and a guard that guessed wrong
would break real work; the model is told what it is doing and decides for itself.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

#: Escalate rather than nag. Three identical calls is worth one quiet note; by the
#: fifth the model has ignored that note, so the reminder names the arguments and says
#: plainly that stopping is an option. Silence after the last threshold is intentional:
#: the model has been told three times and the iteration cap is a few steps away.
DEFAULT_NOTICE_AT = (3, 5, 8)

#: Distinct actions remembered per turn. Far past any real turn, and it keeps a
#: pathological one from growing without bound.
DEFAULT_CAPACITY = 256

_GENTLE = (
    "Note: that makes {count} calls to `{tool}` with exactly these arguments in this "
    "turn, each returning an identical result. Nothing is changing between them."
)

_FIRM = (
    "Note: you have now called `{tool}` {count} times in this turn with exactly these "
    "arguments — {args} — and every call returned an identical result. Another one "
    "will return the same thing. Change the approach, or stop and tell the user "
    "plainly what is blocking you."
)


def canonical_arguments(arguments: Optional[dict[str, Any]]) -> str:
    """A stable string identifying a call's arguments.

    This is the single definition of "the same action" in the codebase. The permission
    engine's one-shot "Allow anyway" grant uses it to decide that a re-proposed call is
    the one the user approved, and the repeat guard uses it to decide that a call is
    going nowhere. If those two ever disagreed, a grant would silently cover something
    the user did not approve.
    """
    try:
        return json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        # Arguments that will not serialise still have to compare somehow, and `str` is
        # stable for the same object graph within one turn — which is all that is asked.
        return str(arguments)


def _digest(value: Any) -> str:
    try:
        payload = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.blake2b(payload.encode("utf-8", "replace"), digest_size=16).hexdigest()


@dataclass(frozen=True)
class Repetition:
    """The action a turn spent itself on."""

    tool: str
    arguments: dict[str, Any]
    count: int


class RepeatGuard:
    """Counts identical (tool, arguments, outcome) triples within one turn."""

    def __init__(
        self,
        *,
        notice_at: tuple[int, ...] = DEFAULT_NOTICE_AT,
        capacity: int = DEFAULT_CAPACITY,
    ) -> None:
        if not notice_at or any(n < 2 for n in notice_at):
            # A threshold of 1 would fire on every first call; 0 or empty would make the
            # guard silent while still costing the bookkeeping.
            raise ValueError("notice_at must be thresholds of 2 or more")
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.notice_at = tuple(sorted(notice_at))
        self.capacity = capacity
        self._seen: "OrderedDict[tuple[str, str, str], int]" = OrderedDict()
        self._worst: Optional[Repetition] = None
        self._exhausted = False

    def reset(self) -> None:
        """Forget the previous turn. Repetition across turns is the user asking again,
        which is a different thing and not the agent's fault."""
        self._seen.clear()
        self._worst = None
        self._exhausted = False

    def observe(
        self,
        tool: str,
        arguments: Optional[dict[str, Any]],
        outcome: Any,
        *,
        status: str = "ok",
    ) -> Optional[str]:
        """Record one completed call. Returns a reminder for the model, or None.

        `outcome` is whatever the model is about to read — a rendered result, a raw
        value, or a denial reason. What matters is that it is the thing the model sees:
        two calls that returned different values did different work, however similar
        the arguments were.
        """
        canon = canonical_arguments(arguments)
        key = (tool, canon, f"{status}:{_digest(outcome)}")

        count = self._seen.get(key, 0) + 1
        self._seen[key] = count
        self._seen.move_to_end(key)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

        if self._worst is None or count > self._worst.count:
            self._worst = Repetition(tool=tool, arguments=dict(arguments or {}), count=count)
        if count >= self.notice_at[-1]:
            # The loop outlived every reminder. Advisory stops here: the engine reads
            # this flag and ends the turn with a checkpoint instead of another note.
            self._exhausted = True

        if count not in self.notice_at:
            return None
        # The first threshold is a quiet observation; every one after it is a reminder
        # that the quiet observation was ignored, and says so with the arguments in hand.
        if count == self.notice_at[0]:
            return _GENTLE.format(tool=tool, count=count)
        return _FIRM.format(tool=tool, count=count, args=_clip(canon))

    @property
    def worst(self) -> Optional[Repetition]:
        """The action repeated most this turn, if any call was seen at all.

        Read when the iteration cap trips, so the user is told what the turn was stuck
        on instead of only that a counter ran out.
        """
        return self._worst

    @property
    def exhausted(self) -> bool:
        """True once some action crossed the LAST threshold — the loop the reminders
        could not break. Advisory ends here; the engine stops the turn with a checkpoint
        rather than spending the remaining budget on a fifth identical answer."""
        return self._exhausted


def _clip(text: str, limit: int = 200) -> str:
    """Arguments go into a reminder the model reads on the hot path — a 20 KB blob
    pasted back at it would crowd out the point being made."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["RepeatGuard", "Repetition", "canonical_arguments", "DEFAULT_NOTICE_AT"]
