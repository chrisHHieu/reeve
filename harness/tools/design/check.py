"""A design check: load a page at each width, measure it, and say what to fix.

`check_page` is the one entry point, used by the `check_design` tool and by the engine after a step
writes a design file. It returns a `DesignReport` that says the same thing three ways:

- `text()`: sentences for the model, each naming the part to change and by how much;
- `facts()`: counts for the session log, which the harness review reads;
- `attach_parts()`: the screenshots, for a model that can look.

Every finding is a measurement (`measure.py`) held against the project's DESIGN.md (`tokens.py`).
None is an opinion, and nothing here says a design is good: "nothing to fix" means nothing measured
was out of bounds.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .behaviour import DARK_PROMISED, Behaviour, has_dark_styles
from .browser import Render, render_page
from .lint import LintFinding, lint_source
from .loads import lines as load_lines, missing_files, unloaded_fonts
from .locks import locked_line
from .measure import MEASURE_JS
from .tokens import DesignTokens, find_design_md, read_tokens
from .versions import changes, say, unasked

#: Desktop, tablet and phone, the widths a design is checked at when none are asked for.
DEFAULT_WIDTHS: tuple[int, ...] = (1440, 834, 390)
#: The bounds on what a caller may ask for.
MIN_WIDTH, MAX_WIDTH, MAX_WIDTHS = 280, 2560, 4

#: The states a page can be drawn in beside it: `pricing.empty.html` is pricing's empty state.
STATES: tuple[str, ...] = ("loading", "empty", "error")

#: How many examples of one kind a sentence lists before it counts the rest.
_SHOWN = 3


def clean_widths(raw: Any) -> list[int]:
    """The widths to check: what was asked, bounded, in the order asked, else the defaults."""
    if not isinstance(raw, (list, tuple)):
        return list(DEFAULT_WIDTHS)
    out: list[int] = []
    for value in raw:
        try:
            width = int(value)
        except (TypeError, ValueError):
            continue
        width = max(MIN_WIDTH, min(MAX_WIDTH, width))
        if width not in out:
            out.append(width)
        if len(out) == MAX_WIDTHS:
            break
    return out or list(DEFAULT_WIDTHS)


@dataclass
class DesignReport:
    """What one check found. `error` set means the page could not be checked, and says why."""

    shown: str
    widths: list[int]
    design_md: Optional[str] = None
    overflow: list[dict[str, Any]] = field(default_factory=list)
    contrast: list[dict[str, Any]] = field(default_factory=list)
    contrast_count: int = 0
    off_colors: list[dict[str, Any]] = field(default_factory=list)
    off_fonts: list[dict[str, Any]] = field(default_factory=list)
    small: list[dict[str, Any]] = field(default_factory=list)
    small_count: int = 0
    small_width: int = 0
    unlabeled: list[str] = field(default_factory=list)
    no_alt: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    #: Local files the page links that are not there, and kit fonts named but never loaded (`loads.py`).
    missing: list[str] = field(default_factory=list)
    unloaded_fonts: list[str] = field(default_factory=list)
    shots: list[tuple[int, bytes]] = field(default_factory=list)
    #: What the page's source shows that a browser cannot (`lint.py`).
    lint: list[LintFinding] = field(default_factory=list)
    #: What the page did when clicked, darkened and given longer text (`behaviour.py`).
    behaviour: Behaviour = field(default_factory=Behaviour)
    #: The page part by part at the narrowest width, and what changed since the last check of it
    #: (`versions.py`); `unasked` is the share of those changes outside what the person pointed at.
    inventory: list[dict[str, Any]] = field(default_factory=list)
    #: What the measuring script returned at each width, with the width: a lock reads its own part
    #: of it (`handoff.py`) and the inventory at every width, not only the narrowest.
    measured_widths: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, str]] = field(default_factory=list)
    unasked: list[dict[str, str]] = field(default_factory=list)
    thumb: bytes = b""
    #: The page was written without design_guide read in the conversation (set by the engine).
    unguided: bool = False
    #: The person locked this page and it is no longer the locked version (`locks.py`): a sentence.
    locked_changed: str = ""
    error: str = ""

    # -- counts ----------------------------------------------------------------------------

    @property
    def issues(self) -> int:
        """Findings the model is asked to fix, one per line it reads: each width that runs off, each
        faint text, and one each for the other kinds. Blocked requests are reported, not counted: a
        page that loads a web font is not broken for asking."""
        return (
            len(self.overflow)
            + len(self.contrast)
            + (1 if self.off_colors else 0)
            + (1 if self.off_fonts else 0)
            + (1 if self.small_count else 0)
            + (1 if self.unlabeled else 0)
            + (1 if self.no_alt else 0)
            + (1 if self.console_errors else 0)
            + (1 if self.missing else 0)
            + (1 if self.unloaded_fonts else 0)
            + len(self.lint_rules)
            + len(self.behaviour.findings()[0])
        )

    @property
    def lint_rules(self) -> set[str]:
        """The source rules the page breaks outright; a "check" finding is said, not counted."""
        return {finding.rule for finding in self.lint if finding.kind == "fix"}

    def facts(self) -> dict[str, Any]:
        """Flat counts for the log row. The names are what the harness review reads."""
        lint = {
            "design_lint": len(self.lint_rules),
            "design_lint_check": sum(1 for finding in self.lint if finding.kind == "check"),
            "design_unguided": int(self.unguided),
            "design_locked_changed": int(bool(self.locked_changed)),
        }
        if self.error:
            return {"design_checked": False, "design_error": self.error[:120], **lint}
        return {
            **lint,
            **self.behaviour.facts(),
            "design_checked": True,
            "design_widths": len(self.widths),
            "design_issues": self.issues,
            "design_overflow": len(self.overflow),
            "design_overflow_phone": sum(1 for item in self.overflow if item["width"] < 600),
            "design_contrast": self.contrast_count,
            "design_off_colors": len(self.off_colors),
            "design_off_fonts": len(self.off_fonts),
            "design_small_targets": self.small_count,
            "design_unlabeled": len(self.unlabeled),
            "design_no_alt": len(self.no_alt),
            "design_console_errors": len(self.console_errors),
            "design_blocked": len(self.blocked),
            "design_missing_files": len(self.missing),
            "design_unloaded_fonts": len(self.unloaded_fonts),
            "design_md": self.design_md is not None,
        }

    # -- what the model reads --------------------------------------------------------------

    def text(self) -> str:
        widths = ", ".join(str(width) for width in self.widths)
        source = [
            f"- {'Line' if finding.kind == 'fix' else 'To check, line'} {finding.line}: {finding.message}."
            for finding in self.lint
        ]
        if self.error:
            head = f"[design check] {self.shown} was not opened: {self.error}."
            if not source:
                return head
            return head + " Its source:\n" + "\n".join(source)
        tried = len(self.behaviour.interactions)
        head = f"[design check] {self.shown} at {widths} px" + (
            f", {tried} control(s) clicked at {self.behaviour.width} px" if tried else ""
        )
        lines: list[str] = []
        for item in self.overflow:
            parts = _examples(item["past"])
            lines.append(
                f"- At {item['width']} px the page is {item['excess']} px wider than the window"
                + (f"; first past the edge: {parts}" if parts else "")
                + "."
            )
        for item in self.contrast[:_SHOWN]:
            lines.append(
                f"- Text under {item['need']:g}:1: {item['where']} is {item['fg']} on {item['bg']}, "
                f"{item['ratio']:g}:1" + (f" ({item['count']} places)" if item["count"] > 1 else "") + "."
            )
        if len(self.contrast) > _SHOWN:
            lines.append(f"- {len(self.contrast) - _SHOWN} more texts under the contrast they need.")
        if self.off_colors:
            listed = "; ".join(f"{item['color']} ({', '.join(item['where'])})" for item in self.off_colors[:_SHOWN])
            more = len(self.off_colors) - _SHOWN
            lines.append(
                f"- Colors {self.design_md} does not name: {listed}" + (f", and {more} more" if more > 0 else "") + "."
            )
        if self.off_fonts:
            listed = "; ".join(f"{item['font']} ({', '.join(item['where'])})" for item in self.off_fonts[:_SHOWN])
            lines.append(f"- Fonts {self.design_md} does not name: {listed}.")
        if self.small_count:
            listed = ", ".join(f"{item['where']} ({item['w']}×{item['h']})" for item in self.small[:_SHOWN])
            touch = 44 if self.small_width < 600 else 24
            lines.append(
                f"- {self.small_count} control(s) under {touch} px at {self.small_width} px: {listed}"
                + (", …" if self.small_count > _SHOWN else "")
                + "."
            )
        if self.unlabeled:
            lines.append(f"- Controls with no name a screen reader can say: {_examples_list(self.unlabeled)}.")
        if self.no_alt:
            lines.append(f"- Images with no alt text: {_examples_list(self.no_alt)}.")
        if self.console_errors:
            lines.append(
                f"- {len(self.console_errors)} script error(s) on load: " + "; ".join(self.console_errors[:2]) + "."
            )
        lines.extend(load_lines(self.missing, self.unloaded_fonts))
        fix, check = self.behaviour.findings()
        lines.extend(f"- {line}." for line in fix)
        lines.extend(source)
        lines.extend(f"- To check: {line}." for line in check)
        if self.locked_changed:
            lines.append(f"- To check: {self.locked_changed}")
        if self.unasked:
            lines.append("- To check: changed outside what the person pointed at: " + "; ".join(say(self.unasked)) + ".")
        if self.blocked:
            hosts = sorted({_host(url) for url in self.blocked})
            lines.append(
                f"- Not loaded, the check runs offline: {len(self.blocked)} request(s) to {', '.join(hosts[:4])}."
                " Fonts or images from there show as fallbacks in the screenshots."
            )
        if self.design_md is None:
            lines.append("- No DESIGN.md: colors and fonts were not checked against one.")
        if self.unguided:
            lines.append(
                "- design_guide has not been read in this conversation: read it, take a direction (or follow"
                " DESIGN.md) and the recipe for this kind of page, and redraw this page by them. This check"
                " measures mistakes, not taste."
            )
        if not self.issues:
            return head + ": nothing to fix." + ("\n" + "\n".join(lines) if lines else "")
        tail = " Screenshots attached." if self.shots else ""
        return head + f": {self.issues} to fix.{tail}\n" + "\n".join(lines)

    def attach_parts(self) -> list[dict[str, Any]]:
        """The screenshots as message parts, each after a line naming its width."""
        parts: list[dict[str, Any]] = []
        for width, png in self.shots:
            if not png:
                continue
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            parts.append({"type": "text", "text": f"{self.shown} at {width} px"})
            parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts


def _host(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0] or url


def _examples(items: list[dict[str, Any]]) -> str:
    shown = [item["where"] + (f" (×{item['count']})" if item.get("count", 1) > 1 else "") for item in items[:_SHOWN]]
    more = len(items) - _SHOWN
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


def _examples_list(items: list[str]) -> str:
    more = len(items) - _SHOWN
    return ", ".join(items[:_SHOWN]) + (f", and {more} more" if more > 0 else "")


def judge(render: Render, *, shown: str, widths: list[int], tokens: DesignTokens, label: str = "") -> DesignReport:
    """Turn what the browser measured into a report. Pure: tested without a browser."""
    named = None if tokens.path is None else (label or tokens.path.name)
    report = DesignReport(shown=shown, widths=widths, design_md=named)
    if render.error:
        report.error = render.error
        return report
    narrowest = min(render.widths, key=lambda rendered: rendered.width) if render.widths else None
    report.inventory = list((narrowest.measured or {}).get("inventory") or []) if narrowest else []
    report.thumb = render.thumb
    report.behaviour = Behaviour(
        width=min(widths) if widths else 0,
        interactions=list(render.interactions),
        dark=render.dark,
        long_text=render.long_text,
        unfinished=list(render.unfinished),
    )
    report.console_errors = list(dict.fromkeys(render.console_errors))[:5]
    report.blocked = list(render.blocked)
    contrast: dict[str, dict[str, Any]] = {}
    colors: dict[str, dict[str, Any]] = {}
    fonts: dict[str, dict[str, Any]] = {}
    narrow: Optional[dict[str, Any]] = None
    for rendered in render.widths:
        measured = rendered.measured or {}
        excess = int(measured.get("scroll_width") or 0) - int(measured.get("width") or rendered.width)
        if excess > 1:
            report.overflow.append({"width": rendered.width, "excess": excess, "past": list(measured.get("past") or [])})
        for item in measured.get("contrast") or []:
            key = f"{item.get('where')}|{item.get('fg')}|{item.get('bg')}"
            seen = contrast.get(key)
            if seen is None or item.get("count", 0) > seen.get("count", 0):
                contrast[key] = dict(item)
        for item in measured.get("colors") or []:
            entry = colors.setdefault(str(item.get("key")), {"color": str(item.get("key")), "where": [], "count": 0})
            entry["count"] += int(item.get("count") or 0)
            for where in item.get("where") or []:
                if len(entry["where"]) < 2 and where not in entry["where"]:
                    entry["where"].append(where)
        for item in measured.get("fonts") or []:
            entry = fonts.setdefault(str(item.get("key")), {"font": str(item.get("key")), "where": [], "count": 0})
            entry["count"] += int(item.get("count") or 0)
            for where in item.get("where") or []:
                if len(entry["where"]) < 2 and where not in entry["where"]:
                    entry["where"].append(where)
        report.measured_widths.append({**measured, "width": rendered.width})
        if narrow is None or rendered.width < narrow["width"]:
            narrow = {"width": rendered.width, **measured}
        if rendered.png:
            report.shots.append((rendered.width, rendered.png))
    report.contrast = sorted(contrast.values(), key=lambda item: item.get("ratio", 0))
    report.contrast_count = sum(int(item.get("count") or 1) for item in report.contrast)
    if tokens.path is not None:
        report.off_colors = sorted(
            (entry for entry in colors.values() if not tokens.names_color(entry["color"])),
            key=lambda entry: -entry["count"],
        )
        report.off_fonts = [entry for entry in fonts.values() if not tokens.names_font(entry["font"])]
    if narrow is not None:
        report.small = list(narrow.get("small") or [])
        report.small_count = int(narrow.get("small_count") or 0)
        report.small_width = int(narrow["width"])
        report.unlabeled = list(narrow.get("unlabeled") or [])
        report.unloaded_fonts = unloaded_fonts(fonts, narrow.get("faces") or [])
        report.no_alt = list(narrow.get("no_alt") or [])
    return report


def check_page(
    path: Path,
    *,
    shown: str = "",
    widths: Optional[Iterable[int]] = None,
    roots: Iterable[Path] = (),
    shots: bool = True,
    browser: Optional[Path] = None,
    previous: Optional[list[dict[str, Any]]] = None,
    asked: Iterable[str] = (),
    script: str = MEASURE_JS,
) -> DesignReport:
    """Check the page at `path` at `widths` (desktop, tablet and phone by default), against the
    nearest DESIGN.md in `roots`. `previous` is the inventory of the last check of the same page, and
    `asked` the parts the person pointed at in the message being answered. `script` is the measure, or
    one that returns it with more besides (a lock's, `handoff.LOCK_JS`). Never raises."""
    chosen = clean_widths(list(widths) if widths is not None else None)
    label = shown or path.name
    design_md = find_design_md(path, list(roots) or [path.parent])
    tokens = read_tokens(design_md)
    md_label = ""
    if design_md is not None:
        md_label = "DESIGN.md"
        for root in roots:
            try:
                md_label = design_md.resolve().relative_to(Path(root).resolve()).as_posix()
                break
            except (OSError, ValueError):
                continue
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    passes = passes_for(source, tokens)
    render = render_page(path, chosen, script, shots=shots, browser=browser, passes=passes)
    report = judge(render, shown=label, widths=chosen, tokens=tokens, label=md_label)
    report.missing = missing_files(render.missing, path)
    stem = path.name.split(".", 1)[0]
    state_pages = any(path.with_name(f"{stem}.{state}{path.suffix}").is_file() for state in STATES)
    report.lint = lint_source(source, design_md_text=tokens.text, state_pages=state_pages or stem != path.stem)
    report.behaviour.dark_missing = bool(DARK_PROMISED.search(tokens.text)) and not has_dark_styles(source)
    report.locked_changed = locked_line(path)
    if previous is not None and report.inventory:
        report.changes = changes(previous, report.inventory)
        report.unasked = unasked(report.changes, [selector for selector in asked if selector])
    return report


def passes_for(source: str, tokens: DesignTokens) -> frozenset[str]:
    """What the browser does besides measuring. Clicking needs something that can answer a click (a
    script, a <details>, a :target rule); dark needs dark styles to load; longer text always runs."""
    passes = {"long_text"}
    lowered = source.lower()
    if "<script" in lowered or "<details" in lowered or ":target" in lowered or "onclick" in lowered:
        passes.add("interact")
    if has_dark_styles(source):
        passes.add("dark")
    return frozenset(passes)
