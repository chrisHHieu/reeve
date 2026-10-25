"""A turn number names ONE turn for the life of a session log.

Every row of the log is addressed `trace → session → turn → step`. The engine
counted turns from zero each time it was built, and a session outlives its engine: the app
restarts, the conversation is reopened the next day. Its log then held `turn 1` twice, and
everything keyed on the number read two turns as one. Real store, 2026-10-04: the number
went back in 56 of 447 logs, and 460 of 1,589 `turn/start` rows carried a number already
used in their log. What that cost:

- `turn_metrics` is keyed `(session_id, turn)` and written with `INSERT OR REPLACE`, so the
  later turn replaced the earlier one in the Metrics tab;
- `harness_kpis --since` dated a turn by the earliest row carrying its number, so the turns a
  resumed session ran AFTER a change were filed before it;
- per-step folds keyed `(turn, step)` merged the calls of different steps.

Two halves. The engine now continues the numbering of the log it adopts (`last_turn`), so a
new row is unique where it is written. Logs already on disk are not rewritten (the log is
append-only): `monotonic_turns` renumbers them on the way IN to a reader, and is the
identity on a log whose numbers never went back.

Pure: events in, events out. No I/O, no clock.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator


def monotonic_turns(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """The same events, with `turn` made unique and increasing along the log.

    A `turn/start` whose number is not above every number seen so far marks an engine that
    started counting again; from there the log's own numbers are shifted past the highest
    one in use, until a start arrives whose own number is already above it (an engine that
    continued the count). Rows between starts take the shift of the turn they belong to,
    which is the one that opened last — a synthetic `turn/end` a new engine writes for a
    turn a dead process left open is written before that engine's first start, so it still
    lands on the turn it closes.

    An event that is not shifted is yielded as the same object.
    """
    highest = 0
    shift = 0
    for event in events:
        turn = event.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool):
            yield event
            continue
        if event.get("type") == "turn/start":
            if turn > highest:
                shift = 0
            elif turn + shift <= highest:
                shift = highest + 1 - turn
        number = turn + shift
        highest = max(highest, number)
        yield event if not shift else {**event, "turn": number}


def last_turn(events: Iterable[dict[str, Any]]) -> int:
    """The number the newest turn of this log answers to; 0 for a log with no turn.

    Read through `monotonic_turns`, so an engine adopting a log that already holds repeats
    continues after the numbers a reader will assign them, not after the raw maximum.
    """
    highest = 0
    for event in monotonic_turns(events):
        if event.get("type") == "turn/start":
            turn = event.get("turn")
            if isinstance(turn, int) and not isinstance(turn, bool):
                highest = max(highest, turn)
    return highest


__all__ = ["last_turn", "monotonic_turns"]
