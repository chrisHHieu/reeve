"""How two versions of one file differ: the regions a diff card draws and the counts a
receipt shows, computed once so the two can never disagree.

Split out of `changes.py`, which answers a different question — what a TURN changed — and
uses these for every file it reports, whether a tool named it or git did.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Files past this size are counted by line-delta sign, not by real diff — a redactor
#: of scope, not of honesty: the receipt says "+some −some" rather than pretending to
#: precision it did not pay for.
_MAX_DIFF_BYTES = 262_144

#: Lines of unchanged text kept either side of an edit. Three, the unified-diff
#: convention, because that is what makes a hunk readable: the two lines above a change
#: are usually how you know which of six similar functions you are looking at.
_DIFF_CONTEXT = 3

#: Changed text past this yields no hunks at all, so a card degrades to the plain row
#: rather than putting a quarter-megabyte of diff on the wire. The receipt's counts do not
#: depend on this — they come from the opcodes, not from the rendered hunk text.
_MAX_HUNK_CHARS = 60_000


def _read_capped(path: str) -> Optional[str]:
    """The file's text, or None when it cannot be read (or is past the cap)."""
    try:
        if Path(path).stat().st_size > _MAX_DIFF_BYTES:
            return None
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _count_lines(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


@dataclass(frozen=True)
class _Region:
    """One changed region of one file, with its surrounding context."""

    old: Optional[str]
    new: str
    added: int
    removed: int
    #: 1-based line where each side's window starts in its file (None: no old side).
    old_start: Optional[int] = None
    new_start: int = 1


def _regions(before: Optional[str], after: Optional[str]) -> list[_Region]:
    """The changed regions between two versions of one file, context included.

    `get_grouped_opcodes` is the grouping `difflib.unified_diff` uses: `_DIFF_CONTEXT`
    lines of unchanged text either side of each edit, and two edits close enough to share
    context arriving as ONE region. Two distant edits stay two regions, which is the
    reason not to return a single flat diff — a card draws a gap between hunks and a
    reader can see there is untouched code in between.

    `before is None` covers both "the file did not exist" and "its prior content could not
    be read". They are deliberately the same value: the card may only claim a removal it
    actually saw, and neither case saw one.
    """
    if after is None:
        return []
    after_lines = after.splitlines()
    if before is None:
        return [_Region(old=None, new=after, added=_count_lines(after) or 0, removed=0)]
    before_lines = before.splitlines()
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    regions: list[_Region] = []
    for group in matcher.get_grouped_opcodes(_DIFF_CONTEXT):
        old_lines: list[str] = []
        new_lines: list[str] = []
        added = removed = 0
        for op, a1, a2, b1, b2 in group:
            # Context lines belong to BOTH sides — that is what makes the two blocks line
            # up when a card prints them one after the other.
            if op in ("equal", "delete", "replace"):
                old_lines.extend(before_lines[a1:a2])
            if op in ("equal", "insert", "replace"):
                new_lines.extend(after_lines[b1:b2])
            if op in ("insert", "replace"):
                added += b2 - b1
            if op in ("delete", "replace"):
                removed += a2 - a1
        # `get_grouped_opcodes` already drops an all-equal group, but a leading-edge
        # adjustment can hand back one that carries no change; a region with no churn is
        # not a region.
        if added or removed:
            regions.append(
                _Region(
                    old="\n".join(old_lines),
                    new="\n".join(new_lines),
                    added=added,
                    removed=removed,
                    # Where the window starts, so a card numbers the file's real lines
                    # instead of restarting at 1 (GUI tools review, B2).
                    old_start=group[0][1] + 1,
                    new_start=group[0][3] + 1,
                )
            )
    return regions


def _diff_counts(before: Optional[str], after: Optional[str]) -> tuple[int, int]:
    """(added, removed) lines between two versions. `None` means "no before-state"
    (a creation) or "unreadable now" — the honest answer is then the line-count delta
    split by sign, not a fabricated diff.

    Derived from the same regions a card renders, so the number on the receipt and the
    lines in the diff can never disagree: counting the opcodes twice from two functions is
    how they would.
    """
    added = removed = 0
    for region in _regions(before, after):
        added += region.added
        removed += region.removed
    return (added, removed)


def hunk_dicts(path: str, before: Optional[str], after: str, budget: int) -> Optional[list[dict]]:
    """`path`'s changed regions as the `{path, old_text, new_text, …}` hunks a diff card
    draws, or None once the running `budget` of changed characters runs out.

    Past the budget the caller drops the whole card rather than truncating it: a diff with
    its middle cut out says something false about the file. Shared by a call's result card
    and its pre-approval preview, so the two draw the same hunks for the same change.
    """
    out: list[dict] = []
    for region in _regions(before, after):
        budget -= len(region.new) + len(region.old or "")
        if budget < 0:
            return None
        out.append(
            {
                "path": path,
                "old_text": region.old,
                "new_text": region.new,
                # The exact churn, carried because the two sides above are CONTEXT-WINDOWED:
                # unchanged lines appear in both, so a surface counting each side would
                # report a one-line edit as `+7 −7`. These are the same numbers the turn's
                # receipt shows, from the same opcodes, so the two can never disagree.
                "added": region.added,
                "removed": region.removed,
                "new_start": region.new_start,
                **({"old_start": region.old_start} if region.old_start else {}),
            }
        )
    return out


def hunk_weight(hunks: list[dict]) -> int:
    """Changed characters a card's hunks already spend of `_MAX_HUNK_CHARS`."""
    return sum(len(h["new_text"]) + len(h["old_text"] or "") for h in hunks)
