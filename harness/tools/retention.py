"""Bounded tool output that says what it dropped — and where the rest went.

Every tool in this codebase already caps what it returns — shell keeps 20,000
characters, `web_fetch` 20,000, `grep` 100 matches of 300 characters each. Nothing was
ever unbounded. Three things were missing, and all three are about honesty rather than
size:

**Each site kept one end.** Shell kept the tail because "builds and test runners put
the verdict at the end" — true, and it lost the command echo and the first error.
`web_fetch` kept the head and lost the conclusion of a long page. Which end matters is
a property of the content, not of the tool, so keeping both ends and dropping the
middle is the only choice that is not a guess.

**The gap was invisible.** A `truncated: True` flag tells a model that something is
missing but not where, not how much, and not that it should say so to the user. A
model that cannot see the size of the hole reasons as if the text were complete —
which is how a truncated log turns into a confident wrong answer. The marker this
module inserts sits AT the cut, in the text the model reads, and carries the count.

**The gap was unrecoverable.** Seeing the hole is not the same as being able to look
inside it. Given a `spill` sink (`tools/spill.py`), the complete text is written once,
at the moment of the cut, and the marker names the path — so a truncated ten-minute
build costs a `read_file`, not a second ten minutes.

The budget covers the WHOLE returned string, marker included. A caller that asks for
at most 20,000 characters is making a context-window promise, and a bound that quietly
returns 20,045 has broken it — small here, but the same reasoning that permits 45
characters permits a megabyte later.

Deliberately character-based, not byte-based. Python strings slice by code point, so
`text[:n]` can never split a character the way a `Uint8Array` can — a byte-oriented
retainer here would be machinery imported for a problem this language does not have.
Sizes are still reported in bytes as well, because "4.2 MB" is what a person
recognises and code points are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

#: The tail gets the larger share by default: an exit status, a stack trace's final
#: frame and a test summary all land at the end, while the head usually only needs
#: enough to identify what ran. Callers that know their content pass their own.
DEFAULT_HEAD_SHARE = 0.35

#: The outer ceiling on ONE tool result: no result occupies more than this of the next
#: request, whatever produced it. 50k characters / 2 000 lines is the per-result
#: line. The engine enforces it (`_enforce_turn_budget`); it is
#: declared here so that a tool can size its own budget inside it.
RESULT_CEILING_CHARS = 50_000
RESULT_CEILING_LINES = 2000
#: Room left for what a render puts around a tool's own text (a header, a status line, a
#: locator). A tool whose budget may be chosen by the model caps it at `BODY_MAX_CHARS`,
#: so its result is cut once, by the tool that knows where its content can be cut, and
#: never a second time by the ceiling. `web_fetch` used to accept 100,000 and deliver
#: 50,000 of them, with two files and a footer that counted only the first cut.
RESULT_ENVELOPE_CHARS = 2_000
BODY_MAX_CHARS = RESULT_CEILING_CHARS - RESULT_ENVELOPE_CHARS
#: The same for the line ceiling, and it is `retain`'s default: a result of short lines can
#: fit any character budget and still be a wall the ceiling cuts. `seq 1 3000` through the
#: shell kept 20,000 characters, which was 2,075 lines; the engine cut 106 more and the
#: model read three statements about one output (session 14715fae-61f, 2026-10-05).
RESULT_ENVELOPE_LINES = 12
BODY_MAX_LINES = RESULT_CEILING_LINES - RESULT_ENVELOPE_LINES

#: A sink is handed the complete text and returns a locator, or None if it could not
#: store it. It is called at most once, and only when something is actually dropped.
Sink = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class Retained:
    """Bounded text plus exactly what was left out, and where to find it."""

    text: str
    omitted_chars: int = 0
    omitted_lines: int = 0
    omitted_bytes: int = 0
    spilled: Optional[str] = None

    @property
    def truncated(self) -> bool:
        return self.omitted_chars > 0


def _marker(chars: int, lines: int, size_bytes: int, saved: Optional[str]) -> str:
    size = (
        f"{size_bytes} B"
        if size_bytes < 1024
        else f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024
        else f"{size_bytes / (1024 * 1024):.1f} MB"
    )
    where = f"{lines:,} line{'' if lines == 1 else 's'}" if lines else f"{chars:,} chars"
    recover = f" — the complete output is at {saved}" if saved else ""
    return f"\n… {where} ({size}) omitted from the middle{recover} …\n"


def _describe(text: str, start: int, end: int, saved: Optional[str]) -> str:
    gap = text[start:end]
    return _marker(
        len(gap), gap.count("\n"), len(gap.encode("utf-8", "replace")), saved
    )


def _line_caps(text: str, max_lines: int, head_share: float) -> tuple[int, int]:
    """The most characters the head and the tail may take for the kept text to stay inside
    `max_lines`: the first lines and the last lines, whole. Three lines are held back, two
    for the marker between them and one because a text that ends in a newline counts an
    empty last line."""
    room = max(0, max_lines - 3)
    head_lines = int(room * head_share)
    tail_lines = room - head_lines
    head = 0
    for _ in range(head_lines):
        cut = text.find("\n", head)
        if cut < 0:
            break
        head = cut + 1
    tail_from = len(text)
    for _ in range(tail_lines):
        cut = text.rfind("\n", 0, max(0, tail_from - 1))
        if cut < 0:
            break
        tail_from = cut + 1
    return head, len(text) - tail_from


def _split(
    text: str,
    budget: int,
    head_share: float,
    saved: Optional[str],
    caps: Optional[tuple[int, int]] = None,
) -> tuple[int, int, str]:
    """Solve for (head, tail, marker) with `head + len(marker) + tail <= budget`.

    The marker's length depends on the size of the gap, and the gap depends on how much
    room the marker leaves. Rather than iterate to a fixed point — which converges but
    is not obviously bounded — the room is allocated against the marker for the LARGEST
    possible gap (the whole text), and the real marker is emitted into it. The real one
    is all but always the same length or shorter, so the reservation costs nothing; on
    the rare input where it is longer, the allowance is cut by exactly the overflow and
    the split is redone, which strictly decreases and therefore terminates.

    Returns `(0, 0, marker)` when the budget cannot hold even the marker.
    """
    total = len(text)
    reserve = _describe(text, 0, total, saved)
    allowance = budget
    for _ in range(4):
        room = max(0, allowance - len(reserve))
        if room == 0:
            return 0, 0, reserve
        head = int(room * head_share)
        tail = room - head
        if caps is not None:
            head, tail = min(head, caps[0]), min(tail, caps[1])
        marker = _describe(text, head, total - tail, saved)
        overflow = head + len(marker) + tail - budget
        if overflow <= 0:
            return head, tail, marker
        allowance -= overflow
    return 0, 0, reserve


def retain(
    text: str,
    *,
    budget: int,
    head_share: float = DEFAULT_HEAD_SHARE,
    spill: Optional[Sink] = None,
    max_lines: Optional[int] = BODY_MAX_LINES,
) -> Retained:
    """Keep the two ends of `text` within `budget` characters and `max_lines` lines,
    naming the gap.

    Both bounds are applied in ONE cut. The line bound is on by default and sized inside
    the engine's per-result ceiling, so what a tool keeps is never cut again for being a
    wall of short lines; pass `max_lines=None` for a text that is not a tool result.

    `budget` bounds the ENTIRE returned string, marker included. Text that already fits
    comes back untouched with no marker and no spill — a bound that announces itself
    when it did nothing trains a model to distrust the announcement, and a sink invoked
    when nothing was dropped writes a file nobody will ever want.
    """
    if budget < 0:
        raise ValueError("budget must be non-negative")
    if not 0.0 <= head_share <= 1.0:
        raise ValueError("head_share must be between 0 and 1")

    if max_lines is not None and max_lines < 3:
        raise ValueError("max_lines must leave room for the marker")

    total = len(text)
    walled = max_lines is not None and text.count("\n") + 1 > max_lines
    if total <= budget and not walled:
        return Retained(text=text)

    saved = _save(spill, text)
    caps = _line_caps(text, max_lines, head_share) if walled else None
    # A text that fits the characters but not the lines is cut by its lines alone: the
    # allowance must then be large enough not to bind.
    budget = max(budget, 0) if total > budget else total + 400
    head, tail, marker = _split(text, budget, head_share, saved, caps)
    # A locator long enough to crowd out the content defeats its own purpose: the point
    # of keeping both ends is that the model can read them. When the path costs more
    # than half the budget the marker drops it and the caller's result dict carries it
    # instead — the text is still recoverable, just not from inside the text.
    if saved and caps is None and head + tail < budget // 2:
        head, tail, marker = _split(text, budget, head_share, None)

    if head + tail == 0:
        # The budget cannot even hold the marker. Say as much as fits rather than
        # returning a lie about what is present.
        return Retained(
            text=marker[:budget],
            omitted_chars=total,
            omitted_lines=text.count("\n"),
            omitted_bytes=len(text.encode("utf-8", "replace")),
            spilled=saved,
        )

    gap = text[head : total - tail] if tail else text[head:]
    kept = text[:head] + marker + (text[total - tail :] if tail else "")
    assert len(kept) <= budget, "retain must never exceed its budget"
    return Retained(
        text=kept,
        omitted_chars=len(gap),
        omitted_lines=gap.count("\n"),
        omitted_bytes=len(gap.encode("utf-8", "replace")),
        spilled=saved,
    )


def _save(spill: Optional[Sink], text: str) -> Optional[str]:
    """Run the sink, and let nothing it does escape.

    Spilling is a recovery aid on the failure path of a tool call that has already
    produced its answer. A sink that raises must not turn a successful command into a
    failed one — the worst honest outcome is the truncation that happened before this
    module could spill at all.
    """
    if spill is None:
        return None
    try:
        saved = spill(text)
    except Exception:
        return None
    return saved or None


__all__ = [
    "BODY_MAX_CHARS",
    "BODY_MAX_LINES",
    "DEFAULT_HEAD_SHARE",
    "RESULT_CEILING_CHARS",
    "RESULT_CEILING_LINES",
    "RESULT_ENVELOPE_CHARS",
    "RESULT_ENVELOPE_LINES",
    "Retained",
    "Sink",
    "retain",
]
