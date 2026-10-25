"""Fallback matchers for every edit tool:
`replace_in_file` matches a fragment (`find_matches`), `apply_patch` a hunk's whole lines
(`find_line_block`), on the same ladder, and a miss is explained by the same
`closest_region` — one notion of "where this text is" for both.

The model writes `old` from what it has in context, and what it has is what `read_file`
showed it: lines prefixed `  12\\t`, CRLF folded to LF, escapes sometimes left literal
because the model typed them in a single-line argument. An exact-match-only editor turns
each of those into a failed call, and a failed edit costs a round-trip plus a re-read —
the most common loop this harness sees.

So the match is tried in a documented ladder, and the rung that matched is REPORTED to the
model (`matched_via`): an edit that silently landed by fuzzy means is one nobody can
reason about later. The ladder stops at the first rung that yields EXACTLY the expected
number of matches — a fallback never overrides an exact match, and ambiguity is still
refused.

Deliberately NOT here, from a longer ladder: quote normalisation (smart vs straight
quotes), indentation-flexible matching, and the ≥0.8-similarity block anchor. Each can
rewrite text the model never quoted, and the first two are cheap to add later if a real
failure asks for them — the third is a different kind of tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: The reader's numbering, exactly as `tools/files.py` writes it (`f"{i:>6}\t{text}"`).
#: Leading spaces, the number, a tab: a needle that copied a whole block carries one per
#: line, and none of them exist in the file.
_LINE_NUMBER = re.compile(r"(?m)^[ \t]*\d+[ \t]*\t")


@dataclass(frozen=True)
class EditMatch:
    """One found occurrence, in the LF-normalised text."""

    start: int
    end: int
    via: str


def strip_line_numbers(text: str) -> str:
    return _LINE_NUMBER.sub("", text)


def unescape(text: str) -> str:
    """`\\n`/`\\t` typed literally (a one-line JSON argument) become real characters."""
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _literal_matches(text: str, needle: str, via: str) -> list[EditMatch]:
    if not needle:
        return []
    out: list[EditMatch] = []
    start = text.find(needle)
    while start != -1:
        out.append(EditMatch(start, start + len(needle), via))
        start = text.find(needle, start + len(needle))
    return out


def _trailing_whitespace_matches(text: str, needle: str) -> list[EditMatch]:
    """Match ignoring trailing spaces/tabs at the end of each line of the needle.

    The allowance is one-directional: the FILE may carry trailing whitespace the model's
    copy does not (editors add it, nobody sees it). Leading indentation still has to match
    exactly — that is the part that says WHERE in the block the text lives.
    """
    lines = needle.split("\n")
    pattern = "\n".join(re.escape(line) + r"[ \t]*" for line in lines)
    try:
        return [
            EditMatch(match.start(), match.end(), "trailing-whitespace")
            for match in re.finditer(pattern, text)
        ]
    except re.error:  # pragma: no cover — escape() cannot produce an invalid pattern
        return []


def find_matches(text: str, old: str, *, expected: int) -> tuple[list[EditMatch], str]:
    """(matches, via) for the first rung that yields exactly `expected` matches.

    ([], "exact") when nothing matched; the caller reports the counts.
    """
    rungs = (
        ("exact", lambda: _literal_matches(text, old, "exact")),
        ("line-numbers", lambda: _literal_matches(text, strip_line_numbers(old), "line-numbers")),
        (
            "escapes",
            lambda: _literal_matches(text, unescape(old), "escapes")
            if "\n" not in old and "\\n" in old
            else [],
        ),
        ("trailing-whitespace", lambda: _trailing_whitespace_matches(text, old)),
    )
    for via, find in rungs:
        matches = find()
        if len(matches) == expected:
            return matches, via
    return [], "exact"


def match_counts(text: str, old: str) -> dict[str, int]:
    """How many occurrences each rung finds — the numbers behind a refusal.

    A model told "found 0" retries the same call; told "found 0 exactly, 1 if the line
    numbers are ignored", it fixes the actual problem.
    """
    return {
        "exact": len(_literal_matches(text, old, "exact")),
        "ignoring line numbers": len(_literal_matches(text, strip_line_numbers(old), "line-numbers")),
        "unescaping": len(_literal_matches(text, unescape(old), "escapes"))
        if "\n" not in old and "\\n" in old
        else 0,
        "ignoring trailing whitespace": len(_trailing_whitespace_matches(text, old)),
    }


def line_of(text: str, offset: int) -> int:
    """The 1-based line an offset falls on."""
    return text.count("\n", 0, offset) + 1


#: How many candidate windows the nearest-region search scores (bounded for big files).
_MAX_CANDIDATES = 50
_CLIP = 120


def closest_region(text: str, needle: str) -> Optional[str]:
    """Where the file most resembles `old`, and the first line that differs — the fact a
    failed edit needs (2026-09-25 audit, W9: "found 0" names no place to look, so the
    model re-reads blind or retries the same text). Windows are anchored on file lines
    that equal a line of `old` once stripped, so the search stays cheap on a big file.
    """
    import difflib

    lines = text.split("\n")
    want = needle.split("\n")
    stripped = {}
    for index, line in enumerate(want):
        if line.strip():
            stripped.setdefault(line.strip(), index)
    starts: list[int] = []
    for number, line in enumerate(lines):
        offset = stripped.get(line.strip()) if line.strip() else None
        if offset is not None and number - offset >= 0 and number - offset not in starts:
            starts.append(number - offset)
        if len(starts) >= _MAX_CANDIDATES:
            break
    if not starts:
        first = next((line for line in want if line.strip()), "")
        close = difflib.get_close_matches(first.strip(), [ln.strip() for ln in lines], n=5, cutoff=0.6)
        starts = [i for i, ln in enumerate(lines) if ln.strip() in close][:_MAX_CANDIDATES]
    best: tuple[float, int] = (0.0, -1)
    for start in starts:
        window = "\n".join(lines[start : start + len(want)])
        ratio = difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio()
        if ratio > best[0]:
            best = (ratio, start)
    ratio, start = best
    if start < 0 or ratio < 0.5:
        return None
    region = lines[start : start + len(want)]
    note = (
        f"the closest text is at lines {start + 1}-{start + len(region)} "
        f"({ratio:.0%} similar)"
    )
    for index, (mine, theirs) in enumerate(zip(want, region)):
        if mine != theirs:
            note += (
                f"; first difference at line {start + index + 1}: `old` has "
                f"{mine[:_CLIP]!r}, the file has {theirs[:_CLIP]!r}"
            )
            break
    else:
        if len(region) < len(want):
            note += "; `old` runs past the end of the file"
    return note


def _same_line(have: str, want: str) -> bool:
    return have == want


def _same_line_ignoring_trailing(have: str, want: str) -> bool:
    """The file's line may carry trailing spaces/tabs the model's copy does not — the same
    one-directional allowance as `_trailing_whitespace_matches`."""
    return have.startswith(want) and not have[len(want):].strip(" \t")


def find_line_block(
    lines: list[str], block: list[str], start: int = 0, *, at_end: bool = False
) -> Optional[tuple[int, str]]:
    """`(index, via)` of the first place at or after line `start` where `block` sits, by
    the same ladder `find_matches` climbs — for edits that name whole lines (an
    `apply_patch` hunk). The first rung that finds the block wins, and within a rung the
    FIRST occurrence at or after `start` does: an envelope's hunks are applied in order,
    each searched from where the previous one ended, so order is what disambiguates.
    `at_end` (a hunk marked `*** End of File`) looks only at the end of the file."""
    if not block:
        return None
    numbered = [strip_line_numbers(line) for line in block]
    rungs = (
        ("exact", block, _same_line),
        ("line-numbers", numbered, _same_line),
        ("trailing-whitespace", block, _same_line_ignoring_trailing),
    )
    last = len(lines) - len(block)
    candidates = [last] if at_end else range(start, last + 1)
    for via, want, same in rungs:
        if via == "line-numbers" and want == block:
            continue
        for index in candidates:
            if index >= start and all(
                same(lines[index + offset], line) for offset, line in enumerate(want)
            ):
                return index, via
    return None


def replacement_for(new: str, via: str) -> str:
    """The replacement text, transformed the same way the needle was.

    Symmetry is the point: a needle matched with its line numbers stripped must not write
    the numbers back, and one matched after unescaping must not leave literal `\\n` in the
    file.
    """
    if via == "line-numbers":
        return strip_line_numbers(new)
    if via == "escapes":
        return unescape(new)
    return new


def apply_matches(text: str, matches: list[EditMatch], replacement: str) -> str:
    """`text` with each match replaced. Matches are non-overlapping and in order."""
    out: list[str] = []
    cursor = 0
    for match in matches:
        out.append(text[cursor : match.start])
        out.append(replacement)
        cursor = match.end
    out.append(text[cursor:])
    return "".join(out)


def describe(via: str) -> Optional[str]:
    """How to say the rung in one clause, or None for an exact match (nothing to say)."""
    return {
        "exact": None,
        "line-numbers": "matched ignoring the line numbers in `old`",
        "escapes": "matched after unescaping \\n/\\t in `old`",
        "trailing-whitespace": "matched ignoring trailing whitespace",
    }.get(via)
