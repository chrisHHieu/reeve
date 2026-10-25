"""A project's DESIGN.md, as far as a check can hold a page to it.

DESIGN.md is prose for the model and the person: the colours, type, spacing and voice a project's
screens follow. A check reads two things out of it that a page can be measured against, the colours
it names and the fonts it names, and nothing else. A colour or font is "named" when it appears in
the file (or in a `tokens.css` beside it) in any spelling a stylesheet would use: `#74358c`,
`#7a3`, `rgb(116, 53, 140)`, a family's name. No schema is asked of the file: a person writes it as
they like, and a token they wrote in a sentence counts as much as one in a table.

The nearest DESIGN.md wins: the one in the design's own folder, else the closest one above it, up
to the root the design is in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

DESIGN_FILE = "DESIGN.md"
TOKENS_FILE = "tokens.css"

#: Families every page may use without DESIGN.md naming them: the generic keywords and the
#: system stacks a browser resolves itself.
GENERIC_FONTS = frozenset(
    {
        "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
        "ui-serif", "ui-monospace", "ui-rounded", "math", "emoji", "fangsong", "-apple-system",
        "blinkmacsystemfont", "segoe ui", "segoe ui emoji", "apple color emoji",
    }
)

#: How far a page colour may be from a named one, per channel, and still be it: a colour written as
#: `rgb()` with rounding, or blended once by the browser, lands a step or two away.
COLOR_TOLERANCE = 3

_HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")
_MAX_READ = 200_000


def _hex_rgb(text: str) -> Optional[tuple[int, int, int]]:
    raw = text.lstrip("#")
    if len(raw) in (3, 4):
        raw = "".join(ch * 2 for ch in raw[:3])
    if len(raw) not in (6, 8):
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


@dataclass
class DesignTokens:
    """What a DESIGN.md names. `path` is None when the project has none."""

    path: Optional[Path] = None
    colors: list[tuple[int, int, int]] = field(default_factory=list)
    text: str = ""

    def names_color(self, color: str) -> bool:
        """Whether `color` is one DESIGN.md names, or a mix of its colours: one with white or black
        (a tint or a shade) or two of them together, the way `color-mix()` and a tinted band make
        them. A real session's badge on a 10% tint of its accent was called a colour DESIGN.md does
        not name (2026-10-19), which pulled the page toward fewer colours than its direction has."""
        rgb = _hex_rgb(color)
        if rgb is None:
            return True  # what cannot be read is not held against the page
        if any(all(abs(a - b) <= COLOR_TOLERANCE for a, b in zip(rgb, known)) for known in self.colors):
            return True
        ends = [(255, 255, 255), (0, 0, 0), *self.colors]
        return any(_on_mix(rgb, known, end) for known in self.colors for end in ends if end != known)

    def names_font(self, family: str) -> bool:
        name = family.strip().strip("'\"").lower()
        if not name or name in GENERIC_FONTS:
            return True
        # As a word, so "Inter" is not found in "interface".
        return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", self.text) is not None


def _on_mix(rgb: tuple[int, int, int], one: tuple[int, int, int], other: tuple[int, int, int]) -> bool:
    """Whether `rgb` lies on the straight mix of `one` and `other`, within the colour tolerance."""
    spans = [b - a for a, b in zip(one, other)]
    widest = max(range(3), key=lambda i: abs(spans[i]))
    if abs(spans[widest]) < 8:
        return False
    share = (rgb[widest] - one[widest]) / spans[widest]
    if not 0.0 <= share <= 1.0:
        return False
    return all(abs(rgb[i] - (one[i] + share * spans[i])) <= COLOR_TOLERANCE for i in range(3))


def find_design_md(start: Path, roots: Iterable[Path]) -> Optional[Path]:
    """The DESIGN.md nearest to `start` (a design file or folder), searching up to the root that
    holds it. None when no root holds `start`, or there is none on the way."""
    start = start.resolve()
    folder = start if start.is_dir() else start.parent
    holder: Optional[Path] = None
    for root in roots:
        try:
            root = root.resolve()
            folder.relative_to(root)
        except (OSError, ValueError):
            continue
        if holder is None or len(root.parts) > len(holder.parts):
            holder = root
    if holder is None:
        return None
    current = folder
    while True:
        candidate = current / DESIGN_FILE
        if candidate.is_file():
            return candidate
        if current == holder or current.parent == current:
            return None
        current = current.parent


def read_tokens(design_md: Optional[Path]) -> DesignTokens:
    """The colours and text of `design_md` and a `tokens.css` beside it. Unreadable files read as
    none: a check is an aside and a broken DESIGN.md must not cost the write it follows."""
    if design_md is None:
        return DesignTokens()
    parts: list[str] = []
    for path in (design_md, design_md.with_name(TOKENS_FILE)):
        try:
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace")[:_MAX_READ])
        except OSError:
            continue
    text = "\n".join(parts)
    colors: list[tuple[int, int, int]] = []
    for match in _HEX.finditer(text):
        rgb = _hex_rgb(match.group(0))
        if rgb is not None:
            colors.append(rgb)
    for match in _RGB.finditer(text):
        values = tuple(min(255, int(group)) for group in match.groups())
        colors.append(values)  # type: ignore[arg-type]
    return DesignTokens(path=design_md, colors=colors, text=text.lower())
