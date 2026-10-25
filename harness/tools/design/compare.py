"""The app as built, held against the design it was built from (step 2).

`compare_design` opens the person's running app at the locked page's widths in the harness's
browser and measures it with the lock's own script (`handoff.LOCK_JS`), so both sides are the same
numbers taken the same way. This module holds the two measures against each other and says it.
Pure: tested without a browser.

What is compared is what a person would see differ, not the markup (the app is built from
components, so its tags and classes are its own):

- **parts**, matched by their label or heading, else by the landmark they are (`header`,
  `footer`): a part of the design the app does not have, a part in another order, a part laid out
  in another number of columns at a width, or of a clearly different height;
- **words**: text of the design the app does not show;
- **colours**: the design's colours the app does not use, and colours the app adds;
- **type**: sizes and families of the design the app does not use;
- **the app's own measure**: what it runs off at a width, and text under its contrast.

A **fix** line is a difference the design settles (a missing part, another layout, a design colour
not used); a **check** line may be right in the app (an added colour, words the app has another way
of saying) and is said, not counted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: How far apart two heights may be and still be the same part: a part's text wraps differently
#: in another font stack, so only a clear difference is said.
_HEIGHT_SLACK_PX = 32
_HEIGHT_SLACK_SHARE = 0.35
#: How far a colour may be per channel and still be the design's (the same as DESIGN.md's).
_COLOR_SLACK = 3
#: How many examples one line names.
_SHOWN = 4
_SPACE = re.compile(r"\s+")


def _norm(text: Any) -> str:
    return _SPACE.sub(" ", str(text or "")).strip().lower()


def part_key(part: dict[str, Any]) -> str:
    """What names a part on either side: its label or heading, else its landmark."""
    named = _norm(part.get("label")) or _norm(part.get("heading"))
    if named:
        return named
    tag = str(part.get("tag") or "")
    return tag if tag in ("header", "footer", "nav", "aside", "form") else ""


def _rgb(color: str) -> Optional[tuple[int, int, int]]:
    raw = color.lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def _near(a: str, palette: list[str]) -> bool:
    one = _rgb(a)
    if one is None:
        return True
    return any(
        other is not None and all(abs(x - y) <= _COLOR_SLACK for x, y in zip(one, other))
        for other in (_rgb(color) for color in palette)
    )


def _colors(system: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Each role's colours with where one is used."""
    out: dict[str, list[tuple[str, str]]] = {}
    for role, entries in (system.get("colors") or {}).items():
        out[role] = [(str(entry.get("key") or ""), str(entry.get("where") or "")) for entry in entries or []]
    return out


@dataclass
class Comparison:
    """What differs between the app at `url` and the design `page`."""

    url: str
    page: str
    widths: list[int]
    locked_at: Optional[int] = None
    #: Each difference as `{id, kind, text}`: `kind` is "fix", "check", or "accepted" (a difference
    #: the person kept as the app has it, by its id in the lock). The id is stable while the
    #: difference is the same one, so an acceptance holds across compares and lapses when it changes.
    findings: list[dict[str, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    accepted_ids: frozenset[str] = frozenset()
    error: str = ""

    def add(self, kind: str, ident: str, text: str) -> None:
        if ident in self.accepted_ids:
            kind = "accepted"
        self.findings.append({"id": ident, "kind": kind, "text": text})

    @property
    def fix(self) -> list[str]:
        return [finding["text"] for finding in self.findings if finding["kind"] == "fix"]

    @property
    def check(self) -> list[str]:
        return [finding["text"] for finding in self.findings if finding["kind"] == "check"]

    @property
    def accepted(self) -> list[str]:
        return [finding["text"] for finding in self.findings if finding["kind"] == "accepted"]

    @property
    def issues(self) -> int:
        return len(self.fix)

    def facts(self) -> dict[str, Any]:
        if self.error:
            return {"compare_checked": False, "compare_error": self.error[:120], "compare_page": self.page[:200]}
        return {
            "compare_checked": True,
            # Which page, so the harness review tells one page's compares from another's.
            "compare_page": self.page[:200],
            "compare_locked": self.locked_at is not None,
            "compare_issues": self.issues,
            "compare_to_check": len(self.check),
            "compare_accepted": len(self.accepted),
            **{f"compare_{name}": count for name, count in self.counts.items()},
        }

    def text(self, when: str = "") -> str:
        widths = ", ".join(str(width) for width in self.widths)
        against = f"{self.page} (locked {when})" if self.locked_at is not None and when else (
            self.page if self.locked_at is not None else f"{self.page}, as it is now (not locked)"
        )
        head = f"[design compare] {self.url} against {against}"
        if self.error:
            return f"{head}: not compared, {self.error}."
        lines = (
            [f"- {line}." for line in self.fix]
            + [f"- To check: {line}." for line in self.check]
            + [f"- Kept as the app has it, by the person: {line}." for line in self.accepted]
        )
        verdict = "the same in what was measured" if not self.fix else f"{len(self.fix)} to fix"
        return f"{head} at {widths} px: {verdict}." + ("\n" + "\n".join(lines) if lines else "")


def _digest(items: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(items)).encode("utf-8")).hexdigest()[:10]


def _example(items: list[str]) -> str:
    more = len(items) - _SHOWN
    return ", ".join(items[:_SHOWN]) + (f", and {more} more" if more > 0 else "")


def _parts(design: list[dict[str, Any]], app: list[dict[str, Any]], out: Comparison) -> None:
    """Parts by name, order, columns and height, width by width (the systems are widest first)."""
    if not design or not app:
        return
    widest_design = design[0].get("sections") or []
    widest_app = app[0].get("sections") or []
    app_keys = [part_key(part) for part in widest_app]
    missing: list[str] = []
    order: list[int] = []
    for part in widest_design:
        key = part_key(part)
        if not key:
            continue
        if key in app_keys:
            order.append(app_keys.index(key))
        else:
            missing.append(f"“{part.get('label') or part.get('heading') or key}”")
    if missing:
        out.add("fix", f"missing:{_digest(missing)}", f"Parts of the design the app does not have: {_example(missing)}")
    out.counts["missing_parts"] = len(missing)
    if order != sorted(order):
        out.add("fix", "order", "The app has the design's parts in another order: " + " → ".join(
            f"“{widest_app[index].get('label') or widest_app[index].get('heading') or part_key(widest_app[index])}”" for index in order
        ))
        out.counts["order"] = 1
    columns = 0
    heights = 0
    for width, design_system, app_system in zip(out.widths, design, app):
        app_parts = {part_key(part): part for part in app_system.get("sections") or [] if part_key(part)}
        for part in design_system.get("sections") or []:
            key = part_key(part)
            other = app_parts.get(key) if key else None
            if other is None:
                continue
            name = f"“{part.get('label') or part.get('heading') or key}”"
            want, got = int(part.get("columns") or 0), int(other.get("columns") or 0)
            if want > 1 or got > 1:
                if want != got:
                    columns += 1
                    out.add("fix", f"columns:{key}@{width}", f"At {width} px {name} is {got} across in the app and {want} in the design")
                    continue
            design_h, app_h = int(part.get("h") or 0), int(other.get("h") or 0)
            if design_h and abs(app_h - design_h) > max(_HEIGHT_SLACK_PX, _HEIGHT_SLACK_SHARE * design_h):
                heights += 1
                out.add("check", f"height:{key}@{width}", f"At {width} px {name} is {app_h} px tall in the app and {design_h} px in the design")
    out.counts["columns"] = columns
    out.counts["heights"] = heights


def _palette(design: dict[str, Any], app: dict[str, Any], out: Comparison) -> None:
    design_colors = _colors(design)
    app_colors = _colors(app)
    every_design = [color for entries in design_colors.values() for color, _ in entries]
    every_app = [color for entries in app_colors.values() for color, _ in entries]
    unused = []
    for role, entries in design_colors.items():
        for color, where in entries:
            if color and not _near(color, every_app):
                unused.append(f"{color} ({role}, `{where}` in the design)")
    if unused:
        out.add("fix", f"colors-unused:{_digest(unused)}", f"Design colors the app does not use: {_example(unused)}")
    added = []
    for role, entries in app_colors.items():
        for color, where in entries:
            if color and not _near(color, every_design):
                added.append(f"{color} ({role}, `{where}`)")
    if added:
        out.add("check", f"colors-added:{_digest(added)}", f"Colors in the app the design does not have: {_example(added)}")
    out.counts["colors_unused"] = len(unused)
    out.counts["colors_added"] = len(added)


def _type(design: dict[str, Any], app: dict[str, Any], out: Comparison) -> None:
    app_sizes = {(_norm(entry.get("family")), round(float(entry.get("size") or 0))) for entry in app.get("type") or []}
    app_families = {family for family, _ in app_sizes}
    families: list[str] = []
    sizes: list[str] = []
    for entry in design.get("type") or []:
        family = _norm(entry.get("family"))
        size = round(float(entry.get("size") or 0))
        if family and family not in app_families:
            if entry.get("family") not in families:
                families.append(str(entry.get("family")))
            continue
        if (family, size) not in app_sizes:
            sizes.append(f"{entry.get('family')} {size} px (`<{entry.get('tag', '')}>` “{entry.get('sample', '')}”)")
    if families:
        out.add("fix", f"fonts:{_digest(families)}", f"Fonts of the design the app does not use: {_example(families)}")
    if sizes:
        out.add("fix", f"sizes:{_digest(sizes)}", f"Type sizes of the design the app does not use: {_example(sizes)}")
    out.counts["fonts"] = len(families)
    out.counts["sizes"] = len(sizes)


def _words(design_inventory: list[dict[str, Any]], app_text: str, out: Comparison) -> None:
    shown = _norm(app_text)
    missing: list[str] = []
    for item in design_inventory:
        text = _norm(item.get("text"))
        if len(text) < 3 or text in shown:
            continue
        label = f"“{str(item.get('text') or '')[:40]}”"
        if label not in missing:
            missing.append(label)
    if missing:
        out.add("check", f"words:{_digest(missing)}", f"Words of the design the app does not show: {_example(missing)}")
    out.counts["words"] = len(missing)


def _app_measure(app: list[dict[str, Any]], widths: list[int], out: Comparison) -> None:
    """The app's own measure, as the check reads a page's: past its own layout width."""
    overflow = 0
    for asked, measured in zip(widths, app):
        excess = int(measured.get("scroll_width") or 0) - int(measured.get("width") or asked)
        if excess > 1:
            overflow += 1
            past = ", ".join(str(item.get("where")) for item in (measured.get("past") or [])[:3])
            out.add("fix", f"overflow@{asked}", f"At {asked} px the app is {excess} px wider than the window" + (f"; first past the edge: {past}" if past else ""))
        faint = measured.get("contrast") or []
        if faint:
            worst = faint[0]
            out.add(
                "check",
                f"contrast@{asked}",
                f"At {asked} px the app has text under its contrast: {worst.get('where')} is {worst.get('fg')} on"
                f" {worst.get('bg')}, {worst.get('ratio')}:1" + (f" ({len(faint)} kinds)" if len(faint) > 1 else "")
            )
    out.counts["overflow"] = overflow


def compare(
    *,
    url: str,
    page: str,
    design_system: list[dict[str, Any]],
    design_inventory: list[dict[str, Any]],
    app_measured: list[dict[str, Any]],
    widths: list[int],
    locked_at: Optional[int] = None,
    accepted: Optional[set[str]] = None,
) -> Comparison:
    """`design_system` is the lock's (or a fresh) handoff measure per width, widest first;
    `design_inventory` the design's parts at its narrowest width; `app_measured` what `COMPARE_JS`
    returned for the app at each of `widths`, in the same order, each with its `handoff` and
    `body_text`."""
    out = Comparison(url=url, page=page, widths=widths, locked_at=locked_at, accepted_ids=frozenset(accepted or ()))
    app_system = [dict(measured.get("handoff") or {}) for measured in app_measured]
    _parts(design_system, app_system, out)
    if design_system and app_system:
        _palette(design_system[0], app_system[0], out)
        _type(design_system[0], app_system[0], out)
    _words(design_inventory, " ".join(str(measured.get("body_text") or "") for measured in app_measured[:1]), out)
    _app_measure(app_measured, widths, out)
    return out
