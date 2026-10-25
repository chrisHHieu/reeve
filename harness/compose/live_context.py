"""The context a turn carries about the present: the clock, the mode, the roots, and what the
features add (the skill menu)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from harness.safety.permissions import PermissionEngine

from .prompts import PLAN_MODE_CONTEXT, permissions_context
from .session_helpers import describe_elapsed

#: A feature's per-turn lines: called with the engine (None before it exists), returning
#: `(label, text)` pairs in the order they are joined.
Contributor = Callable[[Any], list[tuple[str, str]]]


def make_context_provider(
    *,
    permissions: PermissionEngine,
    roots_context: Optional[Callable[[], str]],
    background_status: Optional[Callable[[], str]],
    engine_of: Callable[[], Any],
    contributors: Iterable[Contributor] = (),
) -> tuple[Callable[[], str], dict[str, str]]:
    """The live `<system-context>` provider and the named capture of what it last joined.

    The block is written into the turn's user message (`_stamp_live_context`), because system
    messages in the middle of a thread are not reliable across providers. It has several producers:
    the session's permissions line, or the plan reminder while the overlay is on (both can flip
    mid-session, so they are checked each turn and not baked into the instructions; a switch
    changes the block, which is stated again in full), the live directory list (a multi-root session can gain folders
    mid-session), and the features' contributors after them (the skill menu). The memories and the
    person's rules are not here: they are knowledge, fixed at the start of the session.

    The capture is read by the engine's context panel (the context_breakdown in the usage sidecar),
    which measures "skills" as its own category and folds everything else into meta-context. It is
    kept out of the return type: `context_provider` must keep returning the exact string it always
    did, since tests assert substrings of it and the wire format is pinned."""
    context_parts: dict[str, str] = {}
    contributors = list(contributors)

    def context_provider() -> str:
        # A live clock every turn (owner ruling, 2026-08-20). The environment block's "Today's date"
        # is a snapshot from the start of the session, stale for long-lived or self-waking sessions,
        # and it has no time of day, which absolute scheduling (sleep_until, scheduled tasks) needs
        # to work out wake times.
        now = datetime.now().astimezone()
        clock = f"Now: {now.strftime('%Y-%m-%d %H:%M')} ({now.tzname()})"
        engine = engine_of()
        # The gap since the previous message, when it is long enough to matter. A session resumed
        # after lunch reads very differently from one continued a second later, and the model cannot
        # tell the two apart from a clock alone. It is the same volatile half as the clock (the
        # stable digest leaves it out), so it costs the cached prefix nothing.
        if engine is not None and len(engine.messages) > 1:
            previous = max(
                (m.get("ts") for m in engine.messages[:-1] if isinstance(m.get("ts"), (int, float))),
                default=0.0,
            )
            gap = describe_elapsed(now.timestamp() - previous) if previous else ""
            if gap:
                clock += f" — {gap} since the previous message"
        parts: list[tuple[str, str]] = [("clock", clock)]
        # Volatile like the clock (turn_stamps restates only what moved): the children still in
        # flight, so "did both finish?" is read and not guessed.
        if background_status is not None:
            try:
                running = background_status()
            except Exception:  # live context is an enrichment, and it must not cost the turn
                running = ""
            if running:
                parts.append(("background", running))
        if permissions.plan:
            parts.append(("mode", PLAN_MODE_CONTEXT))
        else:
            line = permissions_context(permissions.mode.value)
            if line:
                parts.append(("mode", line))
        if roots_context is not None:
            roots_text = roots_context()
            if roots_text:
                parts.append(("roots", roots_text))
        for contribute in contributors:
            parts.extend(contribute(engine))
        context_parts.clear()
        context_parts.update({label: text for label, text in parts})
        return "\n\n".join(text for _, text in parts)

    return context_provider, context_parts
