"""The goal ledger: what each round moved, read by code, and the stops it decides.

A goal is the one loop here that runs with nobody watching, and before this module the
only things that ended it were the model's own `done`/`blocked` or the round cap (256 by
default). A round that edited nothing and moved no check opened the next one anyway, and
a model that finished without calling `done` kept going — every round re-reading the
whole session. That is "token blowout"; other agents' `/goal` judges after every turn and Codex's stops on a spent budget.

After every round the driver records one ledger entry (`settle_round`) from facts the
harness already holds — the round's provider spend, the files its change receipt lists,
the tool calls it made, and the standing of the checks read from the transcript — and
decides, in this order:

    complete     the goal is proven by the transcript (`goal_proof.proven`): stop now,
                 without spending another round asking the model to say so;
    budget       the goal's token budget is spent;
    no-progress  `STALL_ROUNDS` consecutive rounds that made no tool call the round
                 before had not already made, changed no file and moved no check;
    continue     otherwise.

"Stuck" is deliberately narrow, because a false stop costs the task: any NEW call counts
as progress, reads and searches included, so a round spent investigating is never
stuck. What is left is a round with no tool call at all, or one that only repeats the
last round's calls (re-running the same red check, re-reading the same file) — the loop
where another round cannot help.

The latest entries also ride in the next round prompt (`progress_lines`), which is how
progress survives a compaction without the model re-reading its own work. Pure: nothing
here runs a command or calls a provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Optional, Sequence

from .goal_proof import check_states
from harness.tools.checks import shown_command

from .verification import last_verdict, last_verification

if TYPE_CHECKING:
    from .goals import Goal

#: Consecutive rounds with no change and no moved check before the goal is blocked
#: `no-progress`. Three matches the floor the model's own `blocked` must clear
#: (`BLOCKED_AFTER_ROUNDS`): the harness gives up no sooner than the model may.
STALL_ROUNDS = 3
#: Entries kept in the goal file; only the newest `PROMPT_TAIL` reach the model.
LEDGER_KEEP = 12
PROMPT_TAIL = 3
#: Changed paths named per entry; the count carries the rest.
NAMED_CHANGES = 4
#: Call signatures kept per entry, to compare with the next round (never shown).
KEPT_CALLS = 64

CONTINUE = "continue"
COMPLETE = "complete"
BUDGET = "budget"
NO_PROGRESS = "no-progress"

#: Round outcomes that did not run to an end of their own; they record spend but never
#: count toward a stall (a stopped round says nothing about whether the work moves).
_CUT_SHORT = frozenset({"interrupted", "error"})


@dataclass(frozen=True)
class RoundFacts:
    """What one finished round did, as the harness measured it."""

    round_no: int
    outcome: str
    tokens: int
    #: Files the round's change receipt lists (written by a file tool, or seen moving in
    #: git after a command); empty when nothing changed or the session keeps no receipt.
    changed: list[str]
    standing: str
    #: One short signature per tool call the round made (`call_signatures`).
    calls: tuple[str, ...] = ()


def fmt_tokens(value: int) -> str:
    value = max(0, int(value or 0))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{round(value / 1_000)}k"
    return str(value)


def clean_ledger(raw: Any) -> list[dict[str, Any]]:
    """The stored entries as plain dicts, newest last, at most `LEDGER_KEEP`."""
    out = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return out[-LEDGER_KEEP:]


def standing_of(goal: "Goal", messages: Any, exempt: Sequence[str] = ()) -> str:
    """The checks' standing as one comparable line: each declared check's state, else the
    newest verification-shaped run and the newest independent verdict. Two rounds with
    the same standing and no change moved nothing."""
    try:
        transcript = list(messages or [])
    except TypeError:
        transcript = []
    if goal.checks:
        return "; ".join(
            f"`{item['command']}` {item['state'].replace('_', ' ')}"
            for item in check_states(goal, transcript, exempt)
        )
    parts: list[str] = []
    newest = last_verification(transcript, exempt)
    if newest is not None:
        state = "passed" if newest.ok else "failed" if newest.ok is False else "exit unknown"
        parts.append(f"`{shown_command(newest.command)}` {state}")
    verdict = last_verdict(transcript, exempt)
    if verdict is not None:
        parts.append(f"verify {verdict.verdict}")
    return "; ".join(parts)


def changes_of(engine: Any) -> list[str]:
    """The files the finished turn's receipt lists; empty when there is no receipt."""
    changes = getattr(engine, "changes", None)
    if changes is None:
        return []
    try:
        summary = changes.summary()
    except Exception:
        return []
    return [*summary.created, *summary.overwritten, *summary.deleted]


def call_signatures(messages: Any) -> tuple[str, ...]:
    """One signature per tool call since the newest goal-round message: the tool's name
    and a hash of its arguments, so the same call made again has the same signature."""
    try:
        ordered = list(messages or [])
    except TypeError:
        return ()
    start = next(
        (
            i
            for i in range(len(ordered) - 1, -1, -1)
            if ordered[i].get("role") == "user"
            and (ordered[i].get("source") or {}).get("kind") == "goal_round"
        ),
        -1,
    )
    out: list[str] = []
    for message in ordered[start + 1 :]:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, sort_keys=True, default=str)
            digest = hashlib.sha1(" ".join(arguments.split()).encode("utf-8")).hexdigest()[:10]
            out.append(f"{function.get('name') or '?'}:{digest}")
    return tuple(out)


@dataclass(frozen=True)
class RoundCapture:
    """The round's engine-side facts, taken at the round's end BEFORE the idle edge: once
    the session reads idle a person's turn may claim the engine, and turn start resets
    both the spend counter and the change receipt."""

    tokens: int
    changed: list[str]
    calls: tuple[str, ...]
    messages: list[Any]


def capture_round(engine: Any) -> RoundCapture:
    try:
        tokens = int(getattr(engine, "turn_tokens", 0) or 0)
    except (TypeError, ValueError):
        tokens = 0
    messages = list(getattr(engine, "messages", None) or [])
    return RoundCapture(
        tokens=tokens,
        changed=changes_of(engine),
        calls=call_signatures(messages),
        messages=messages,
    )


def settle_round(goal: "Goal", facts: RoundFacts, *, proven: bool) -> str:
    """Record the round on `goal` (ledger, spend, stall count) and say what happens next.

    Mutates the goal; the caller saves it. A cut-short round records its spend and
    decides nothing — the driver's own pause rule owns that outcome."""
    goal.tokens_used = max(0, int(goal.tokens_used or 0)) + max(0, int(facts.tokens or 0))
    last = goal.ledger[-1] if goal.ledger else {}
    cut_short = facts.outcome in _CUT_SHORT
    if cut_short:
        moved: Optional[bool] = None
    else:
        new_calls = set(facts.calls) - {str(c) for c in last.get("calls") or []}
        moved = (
            bool(new_calls)
            or bool(facts.changed)
            or facts.standing != str(last.get("standing") or "")
        )
    if moved is True:
        goal.stalled_rounds = 0
    elif moved is False:
        goal.stalled_rounds = int(goal.stalled_rounds or 0) + 1
    entry: dict[str, Any] = {
        "round": facts.round_no,
        "outcome": facts.outcome,
        "tokens": max(0, int(facts.tokens or 0)),
        "changed": list((facts.changed or [])[:NAMED_CHANGES]),
        "changed_n": len(facts.changed or []),
        "standing": facts.standing,
    }
    entry["calls_n"] = len(facts.calls)
    if facts.calls:
        entry["calls"] = list(dict.fromkeys(facts.calls))[:KEPT_CALLS]
    if moved is not None:
        entry["moved"] = moved
    goal.ledger = clean_ledger([*goal.ledger, entry])
    if cut_short:
        return CONTINUE
    if proven:
        return COMPLETE
    if budget_spent(goal):
        return BUDGET
    if goal.stalled_rounds >= STALL_ROUNDS:
        return NO_PROGRESS
    return CONTINUE


def budget_spent(goal: "Goal") -> bool:
    budget = int(goal.token_budget or 0)
    return budget > 0 and int(goal.tokens_used or 0) >= budget


def _entry_line(entry: dict[str, Any]) -> str:
    count = int(entry.get("changed_n") or 0)
    # File names only: the full paths are in the goal file, and every character here is
    # re-read by each later round.
    named = [PurePath(str(item)).name for item in entry.get("changed") or []]
    if count:
        more = f" +{count - len(named)} more" if count > len(named) else ""
        did = f"changed {count} file(s) ({', '.join(named)}{more})"
    else:
        did = "changed no file"
    calls = int(entry.get("calls_n") or 0)
    did += f", {calls} tool call(s)" if calls else ", no tool call"
    standing = str(entry.get("standing") or "") or "no check has run"
    outcome = str(entry.get("outcome") or "")
    tail = f"; ended {outcome}" if outcome in _CUT_SHORT else ""
    return f"- Round {entry.get('round')}: {did}; checks: {standing}{tail}."


def progress_lines(goal: "Goal") -> list[str]:
    """The round prompt's harness-recorded progress: the newest entries, the stall
    warning while one is building, and the budget while one is set. Empty on round one,
    so a fresh goal's prompt is unchanged."""
    lines: list[str] = []
    tail = goal.ledger[-PROMPT_TAIL:]
    if tail:
        lines.append("Progress recorded by the harness (latest rounds):")
        lines.extend(_entry_line(entry) for entry in tail)
    stalled = int(goal.stalled_rounds or 0)
    if stalled:
        lines.append(
            f"The last {stalled} round(s) made no new tool call, changed no file and "
            "moved no check. The goal "
            f"stops as blocked (no-progress) after {STALL_ROUNDS}. Change approach now; "
            'if only the user can unblock this, call goal(action="blocked", reason="...").'
        )
    if int(goal.token_budget or 0) > 0:
        lines.append(
            f"Token budget: {fmt_tokens(goal.tokens_used)} of "
            f"{fmt_tokens(goal.token_budget)} used; the goal stops when it is spent."
        )
    return lines


__all__ = [
    "BUDGET",
    "COMPLETE",
    "CONTINUE",
    "NO_PROGRESS",
    "RoundCapture",
    "RoundFacts",
    "STALL_ROUNDS",
    "budget_spent",
    "capture_round",
    "changes_of",
    "clean_ledger",
    "fmt_tokens",
    "progress_lines",
    "settle_round",
    "standing_of",
]
