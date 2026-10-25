"""A guard on the model's own output, read before more of it is requested.

Output-token continuation extends an answer
the provider cut at the output limit. When the cut happened inside a repetition loop,
the extension buys a LONGER loop and spends a whole extra request doing it. A well-known
guard (`#86581`) checks the fragment first: a 60-char window covering at least half the
text, or one line five times over, is a loop — the truncated answer is reported as
final instead.

Deliberately fail-open below `MIN_CHARS`: a short fragment cannot show a loop, and this
must never stop a real answer.
"""

from __future__ import annotations

from collections import Counter

#: Shorter than this cannot show a loop worth refusing to extend.
MIN_CHARS = 400
#: The window whose repetition counts as domination.
WINDOW = 60
#: One line this many times over is a loop regardless of window coverage.
LINE_REPEATS = 5


def repetition_dominated(
    text: str, *, min_chars: int = MIN_CHARS, window: int = WINDOW
) -> bool:
    """True when `text` is mostly one repeated window or one repeated line."""
    if len(text) < min_chars:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and max(Counter(lines).values()) >= LINE_REPEATS:
        return True
    sample = text[:window]
    if sample.strip() and text.count(sample) * len(sample) >= 0.5 * len(text):
        return True
    return False
