"""What the page did when it was used, as findings (phase 2).

`interact.py` clicks, darkens and stretches the page and reports what it saw; this holds that
against the craft rules `design_guide` gives (`guide.py`, "Interaction", "Color", "States") and says
it as sentences. Pure: tested without a browser.

Two kinds, as in `lint.py`: a **fix** line breaks a rule the model was given and is counted in the
check's `issues`; a **check** line is said and not counted.

A control that does nothing when clicked is a fix. It used to be a check, because a button
is fine in a static mockup and the check could not tell which a page was; and on the store
(2026-10-19..20) every design's last version still had 3-5 of its 4-6 tried controls doing nothing,
under "0 issues". A page now says when it is only a picture of a screen
(`<meta name="design-fidelity" content="static">`), and only then is a still button a check.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Optional

#: A thing that moves passes through more than its first and last state; two states is a jump.
_JUMP = 2


@dataclass
class Behaviour:
    """What was done to the page and what it did."""

    width: int = 0
    interactions: list[dict[str, Any]] = field(default_factory=list)
    dark: Optional[dict[str, Any]] = None
    dark_missing: bool = False
    long_text: Optional[dict[str, Any]] = None
    unfinished: list[str] = field(default_factory=list)
    #: The page declares itself a static mockup (`is_static`): its controls may stay still.
    static: bool = False

    def findings(self) -> tuple[list[str], list[str]]:
        """`(fix, check)`: sentences, each with no leading dash."""
        fix: list[str] = []
        check: list[str] = []
        idle: list[str] = []
        for entry in self.interactions:
            where = entry.get("where") or "a control"
            opened = entry.get("opened")
            if not opened:
                if entry.get("dead"):
                    if entry.get("promises"):
                        fix.append(f"{where} says it opens something (aria-expanded, aria-controls or aria-haspopup) and clicking it changed nothing")
                    else:
                        idle.append(where)
                continue
            if int(entry.get("open_steps") or 0) <= _JUMP:
                fix.append(f"Clicking {where} shows {opened} at once; menus and dialogs move in (150–250 ms, opacity and transform)")
            if entry.get("modal") and entry.get("focus_inside") is False:
                fix.append(f"Focus stays behind {opened} when it opens; move it into the dialog")
            esc, outside = entry.get("esc"), entry.get("outside")
            if not esc and outside is False:
                fix.append(f"Nothing closes {opened}: not Esc, not a click outside")
            else:
                if not esc:
                    fix.append(f"Esc does not close {opened}")
                if outside is False:
                    fix.append(f"A click outside does not close {opened}")
            steps = entry.get("close_steps")
            if steps is not None and int(steps) <= _JUMP:
                fix.append(f"{opened} vanishes at once when it closes; it should move out the way it came in")
        if idle:
            shown = ", ".join(idle[:3]) + (f" and {len(idle) - 3} more" if len(idle) > 3 else "")
            if self.static:
                check.append(f"Clicking {shown} changed nothing on the page, which is declared a static mockup")
            else:
                fix.append(
                    f"Clicking {shown} changed nothing on the page. Make each one answer (a state, a panel, "
                    'a message) or a real link, or, if the page is only a picture of a screen, say so with '
                    '<meta name="design-fidelity" content="static">'
                )
        if self.dark_missing:
            fix.append("DESIGN.md speaks of a dark palette and the page has no dark styles (@media (prefers-color-scheme: dark))")
        for item in (self.dark or {}).get("contrast") or []:
            fix.append(
                f"In dark mode, text under {item['need']:g}:1: {item['where']} is {item['fg']} on {item['bg']}, {item['ratio']:g}:1"
            )
            break
        if self.dark:
            excess = int(self.dark.get("scroll_width") or 0) - int(self.dark.get("width") or self.width)
            if excess > 1:
                fix.append(f"In dark mode the page runs {excess} px off at {self.width} px")
        stretched = self.long_text or {}
        excess = int(stretched.get("excess") or 0)
        spill = list(stretched.get("spill") or [])
        if stretched.get("changed") and (excess > 1 or spill):
            said = f"With longer text (a long German word in labels, a Vietnamese sentence in headings) at {self.width} px"
            parts = []
            if excess > 1:
                past = [item for item in stretched.get("past") or [] if isinstance(item, dict) and item.get("where")]
                where = ", ".join(f"{item['where']} by {int(item.get('by') or 0)} px" for item in past[:3])
                parts.append(f"the page runs {excess} px off" + (f" (past the edge: {where})" if where else ""))
            if spill:
                parts.append("text spills out of " + ", ".join(spill[:3]))
            fix.append(said + ", " + " and ".join(parts) + "; let it wrap, or truncate with an ellipsis and the full text on hover")
        if self.unfinished:
            check.append("Not finished in time: " + ", ".join(self.unfinished))
        return fix, check

    def facts(self) -> dict[str, Any]:
        fix, check = self.findings()
        return {
            "design_controls_tried": len(self.interactions),
            "design_behaviour": len(fix),
            "design_dead_clicks": sum(1 for entry in self.interactions if entry.get("dead")),
            "design_static": self.static,
            "design_dark_checked": self.dark is not None,
            "design_long_text": int(any(line.startswith("With longer text") for line in fix)),
            "design_unfinished": len(self.unfinished),
        }


_DARK_STYLES = re.compile(r"prefers-color-scheme\s*:\s*dark|\[data-theme\s*=\s*[\"']?dark|\.dark\b|color-scheme\s*:[^;]*dark", re.I)


#: A page that says it is a static mockup, the attributes in either order.
_META = re.compile(r"<meta\b[^>]*>", re.I)


def is_static(source: str) -> bool:
    """Whether the page declares `<meta name="design-fidelity" content="static">`."""
    for tag in _META.findall(source or ""):
        lowered = tag.lower().replace("'", '"')
        if 'name="design-fidelity"' in lowered and 'content="static"' in lowered:
            return True
    return False


#: DESIGN.md promising a dark scheme, not merely naming a dark colour ("dark grey ink").
DARK_PROMISED = re.compile(r"\bdark[ -](?:mode|theme|palette|scheme|variant)\b", re.I)


def has_dark_styles(source: str) -> bool:
    """Whether the page carries styles for a dark scheme of any of the usual shapes."""
    return bool(_DARK_STYLES.search(source))
