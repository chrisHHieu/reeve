"""A locked design and the handoff measured from it.

A design is drawn as one page under `design/`; an app is something else, built from components, a
theme and routes. Between the two, what the page *is* must be said once, in terms the coding agent
can hold the app to: its colours by the role each plays, its type, its spacing, its parts from top
to bottom at each width, what clicking it does and how its motion runs.

That is what a browser can measure, so the harness measures it, when the person locks the page:
`HANDOFF_JS` runs in the same load as the check (`check.check_page(script=...)`) at the three
widths, and `lock_page` turns what it returned into two files the harness writes and no model does:

- `design/.locks/<page>.json`: the lock. Which version of the page (its sha256), when, what the
  check still said and the person accepted, and what was measured (for comparing the app with the
  design later, and for telling a later change to the page from the locked one).
- `<page>.handoff.md` beside the page: the same, as the brief the coding agent builds from.

Nothing here says which component of the app a part becomes: that is read from the app, by the agent,
and proposed to the person before it codes.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .app_read import AppFacts, read_app
from .check import DEFAULT_WIDTHS, DesignReport, check_page
from .handoff_app import app_lines, app_summary, color_cell, font_cell
from .handoff_js import HANDOFF_JS
from .measure import MEASURE_JS
from .locks import LOCK_VERSION, LOCKS_DIR, design_folder, handoff_path, lock_path, page_sha, read_lock
from .tokens import DesignTokens, find_design_md, read_tokens


#: Both scripts in one load: the check's measure and the handoff's, under `handoff`.
LOCK_JS = f"Object.assign({MEASURE_JS.strip()}, {{ handoff: {HANDOFF_JS.strip()} }})"


@dataclass
class Locked:
    """What locking a page measured. `error` set means the page could not be opened."""

    page: Path
    shown: str
    sha256: str
    report: DesignReport
    #: The handoff measure at each width, widest first.
    system: list[dict[str, Any]] = field(default_factory=list)
    tokens: DesignTokens = field(default_factory=DesignTokens)
    #: What the project's app already has, read from its files (`app_read.py`).
    app: AppFacts = field(default_factory=AppFacts)
    error: str = ""


def measure_for_lock(
    page: Path,
    *,
    shown: str,
    roots: Iterable[Path],
    widths: Optional[Iterable[int]] = None,
    browser: Optional[Path] = None,
) -> Locked:
    """Open the page at the widths (the check's three by default) and measure it for its lock."""
    chosen = list(widths) if widths is not None else list(DEFAULT_WIDTHS)
    root_list = list(roots)
    sha = page_sha(page)
    report = check_page(page, shown=shown, widths=chosen, roots=root_list, shots=False, browser=browser, script=LOCK_JS)
    tokens = read_tokens(find_design_md(page, root_list or [page.parent]))
    app = read_app(root_list[0]) if root_list else AppFacts()
    locked = Locked(page=page, shown=shown, sha256=sha, report=report, tokens=tokens, app=app, error=report.error)
    if not report.error:
        # Each width's measure under the width it was asked at, widest first. A page with no viewport
        # tag lays out at 980 px in a phone's window, so the page's own width would misorder them.
        locked.system = [
            {**dict(width.get("handoff") or {}), "width": int(width["width"])}
            for width in sorted(report.measured_widths, key=lambda measured: -int(measured.get("width") or 0))
        ]
    return locked


#: Lines of a check that are said and not counted: nothing to accept.
_SAID_ONLY = ("- To check", "- No DESIGN.md", "- Not loaded", "- design_guide has not been read")


def accepted_lines(report: DesignReport) -> list[str]:
    """What the check still says, line by line: what the person accepts by locking anyway."""
    if report.error or not report.issues:
        return []
    lines = report.text().splitlines()[1:]
    return [line[2:] for line in lines if line.startswith("- ") and not line.startswith(_SAID_ONLY)][:20]


def lock_record(locked: Locked, *, handoff: str, now: Optional[float] = None) -> dict[str, Any]:
    """The lock file's content. Pure."""
    report = locked.report
    return {
        "v": LOCK_VERSION,
        "page": locked.shown,
        "sha256": locked.sha256,
        "locked_at": int((now if now is not None else time.time()) * 1000),
        "widths": list(report.widths),
        "issues": report.issues,
        "accepted": accepted_lines(report),
        "handoff": handoff,
        "design_md": report.design_md,
        "system": locked.system,
        "inventory": {str(width.get("width")): width.get("inventory") or [] for width in report.measured_widths},
        "interactions": list(report.behaviour.interactions),
    }


# -- the handoff, as the coding agent reads it -------------------------------------------------


def _row(*cells: Any) -> str:
    return "| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells) + " |"


def _when(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def _colors(system: dict[str, Any], tokens: DesignTokens, app: AppFacts) -> list[str]:
    colors = system.get("colors") or {}
    named = tokens.path is not None
    extra_heads = (["In DESIGN.md"] if named else []) + (["In the app"] if app.found else [])
    head = [_row("Role", "Color", "Used", "Example", *extra_heads)]
    head.append("|" + "---|" * (4 + len(extra_heads)))
    out = list(head)
    words = {"background": "Background", "text": "Text", "border": "Border"}
    for role in ("background", "text", "border"):
        for entry in colors.get(role) or []:
            color = str(entry.get("key") or "")
            used = f"{entry.get('count', 0)}×"
            extra = (["yes" if tokens.names_color(color) else "**no, add it**"] if named else []) + (
                [color_cell(app, color)] if app.found else []
            )
            out.append(_row(words[role], f"`{color}`", used, f"`{entry.get('where', '')}`", *extra))
    return out if len(out) > 2 else []


def _type(system: dict[str, Any], tokens: DesignTokens, app: AppFacts) -> list[str]:
    rows = system.get("type") or []
    if not rows:
        return []
    fonts = app.found and bool(app.fonts)
    out = [
        _row("Font", "Size", "Line height", "Weight", "Tracking", "Used", "Example", *(["In the app"] if fonts else [])),
        "|" + "---|" * (8 if fonts else 7),
    ]
    for entry in rows:
        line = entry.get("line")
        out.append(
            _row(
                entry.get("family", ""),
                f"{entry.get('size', 0):g} px",
                line if line == "normal" else f"{line:g} px" if isinstance(line, (int, float)) else line,
                entry.get("weight", 400),
                f"{entry.get('tracking', 0):g} px",
                f"{entry.get('count', 0)}× `<{entry.get('tag', '')}>`",
                f"“{entry.get('sample', '')}”",
                *([font_cell(app, str(entry.get("family", "")))] if fonts else []),
            )
        )
    return out


def _sections(system_by_width: list[dict[str, Any]]) -> list[str]:
    if not system_by_width or not system_by_width[0].get("sections"):
        return []
    widths = [int(system.get("width") or 0) for system in system_by_width]
    out = [_row("#", "Part", "Heading or label", *(f"At {width} px" for width in widths)), "|" + "---|" * (3 + len(widths))]
    widest = system_by_width[0]["sections"]
    for index, part in enumerate(widest, start=1):
        cells = []
        for system in system_by_width:
            match = next((other for other in system.get("sections") or [] if other.get("name") == part.get("name")), None)
            if match is None:
                cells.append("not shown")
                continue
            columns = int(match.get("columns") or 0)
            cells.append(f"{match.get('h', 0)} px tall" + (f", {columns} across" if columns > 1 else ""))
        out.append(_row(index, f"`{part.get('name', '')}`", part.get("label") or part.get("heading") or "", *cells))
    return out


def _interactions(interactions: list[dict[str, Any]], width: int) -> list[str]:
    out: list[str] = []
    for entry in interactions:
        where = entry.get("where") or "a control"
        opened = entry.get("opened")
        if not opened:
            if not entry.get("dead"):
                out.append(f"- Clicking `{where}` changes the page in place.")
            continue
        closes = [how for how, key in (("Esc", "esc"), ("a click outside", "outside")) if entry.get(key)]
        steps = int(entry.get("open_steps") or 0)
        line = f"- Clicking `{where}` opens `{opened}`" + (" (a dialog: focus moves into it)" if entry.get("modal") and entry.get("focus_inside") else "")
        line += f", moving in over {steps} frames" if steps > 2 else ", shown at once"
        line += f"; it closes with {' and '.join(closes)}" if closes else "; nothing closes it"
        out.append(line + ".")
    if out:
        out.insert(0, f"Clicked by Reeve's browser at {width} px, each on a fresh load:")
    return out


def handoff_markdown(locked: Locked, record: dict[str, Any]) -> str:
    """The handoff the coding agent builds from. Pure: every line is a measurement or a rule."""
    report = locked.report
    widest = locked.system[0] if locked.system else {}
    widths = ", ".join(str(width) for width in report.widths)
    lock_rel = record.get("lock_file") or "design/.locks/"
    lines: list[str] = [
        f"# Handoff: {locked.shown}",
        "",
        f"Locked by the person on {_when(int(record['locked_at']))}. Measured by Reeve's own browser at {widths} px;"
        " no model wrote this file. Reeve rewrites it when the page is locked again.",
        "",
        f"- Locked page: `{locked.shown}` (sha256 `{locked.sha256[:12]}`; lock: `{lock_rel}`)",
        f"- DESIGN.md: `{report.design_md}`" if report.design_md else "- DESIGN.md: none in this project",
        "",
        "## How to build from this",
        "",
        "1. Read the app first: its framework, its routes, its components and where its theme lives"
        " (Tailwind config, CSS variables, a theme file).",
        "2. Before writing code, tell the person which part below becomes which existing component, which"
        " parts need a new one, where the new colors and fonts go, and the steps; wait for their go.",
        "3. Reuse a component where one fits; change it without breaking its other uses. Colors, sizes"
        " and fonts go into the app's theme as tokens, never as loose values in a component.",
        "4. Keep every state and motion listed below. Match the page at each width it was measured at.",
        "5. Run the app's typecheck and tests, start the app, and call `compare_design` with the route's"
        f" address and `{locked.shown}`; fix what it reports and call it again before saying it is done.",
    ]
    lines += app_lines(locked.app)
    colors = _colors(widest, locked.tokens, locked.app)
    if colors:
        lines += ["", f"## Colors (at {widest.get('width')} px; backgrounds by the area they paint)", "", *colors]
    variables = widest.get("vars") or []
    if variables:
        lines += ["", "## The page's own tokens (custom properties on :root)", ""]
        lines += [f"- `{entry['name']}: {entry['value']}`" for entry in variables[:40]]
    type_rows = _type(widest, locked.tokens, locked.app)
    if type_rows:
        lines += ["", "## Type", "", *type_rows]
        narrow = locked.system[-1] if len(locked.system) > 1 else None
        if narrow:
            shrink = [
                f"{big.get('family')} {big.get('size'):g} → {small.get('size'):g} px"
                for big, small in zip(widest.get("type") or [], narrow.get("type") or [])
                if big.get("family") == small.get("family") and big.get("size") != small.get("size")
            ][:4]
            if shrink:
                lines.append(f"\nAt {narrow.get('width')} px the largest sizes become: " + "; ".join(shrink) + ".")
    faces = widest.get("faces") or []
    if faces:
        lines += ["", "## Font files the page loads (copy them into the app's public files)", ""]
        for face in faces:
            where = ", ".join(f"`{url}`" for url in face.get("urls") or []) or "no local file"
            weight = f" {face['weight']}" if face.get("weight") else ""
            style = f" {face['style']}" if face.get("style") and face["style"] != "normal" else ""
            lines.append(f"- {face.get('family', '')}{weight}{style}: {where}")
    spacing = widest.get("spacing") or []
    if spacing:
        lines += ["", "## Spacing", "", "Paddings, margins and gaps in use, smallest first (how often): "
                  + " · ".join(f"{entry['px']:g} ({entry['count']})" for entry in spacing) + "."]
    radii = widest.get("radii") or []
    shadows = widest.get("shadows") or []
    if radii or shadows:
        lines += ["", "## Corners and shadows", ""]
        if radii:
            lines.append("- Corners: " + " · ".join(f"{entry['value']} ({entry['count']})" for entry in radii))
        lines += [f"- Shadow `{entry['value']}` on `{entry.get('where', '')}` ({entry['count']})" for entry in shadows]
    breakpoints = widest.get("breakpoints") or []
    if breakpoints:
        lines += ["", "## Where the layout changes", "", "Media queries in the page: " + ", ".join(f"{point} px" for point in breakpoints) + "."]
    sections = _sections(locked.system)
    if sections:
        lines += ["", "## Parts, top to bottom", "", *sections]
    interactions = _interactions(list(report.behaviour.interactions), report.behaviour.width)
    if interactions:
        lines += ["", "## What clicking does", "", *interactions]
    motion = widest.get("motion") or []
    if motion:
        lines += ["", "## Motion", "", _row("Kind", "What", "Duration", "Easing", "Example"), "|---|---|---|---|---|"]
        lines += [
            _row(entry.get("kind", ""), f"`{entry.get('what', '')}`", f"{entry.get('ms', 0)} ms", f"`{entry.get('easing', '')}`", f"`{entry.get('where', '')}`")
            for entry in motion
        ]
    accepted = record.get("accepted") or []
    if accepted:
        lines += ["", "## Accepted at lock", "", "The check still said this when the person locked the page; they accepted it as it is:", ""]
        lines += [f"- {line}" for line in accepted]
    return "\n".join(lines) + "\n"


def write_lock(locked: Locked, *, now: Optional[float] = None) -> dict[str, Any]:
    """Write the lock and the handoff. Returns the record, with the two files' paths as written."""
    where = lock_path(locked.page)
    if where is None:
        raise ValueError("only a page under a design/ folder can be locked")
    handoff = handoff_path(locked.page)
    shown_dir = locked.shown.rsplit("/", 1)[0] + "/" if "/" in locked.shown else ""
    record = lock_record(locked, handoff=shown_dir + handoff.name, now=now)
    folder = design_folder(locked.page)
    assert folder is not None
    shown_folder = _shown_folder(locked.shown, locked.page, folder)
    record["lock_file"] = shown_folder + LOCKS_DIR + "/" + where.name
    # What the person kept of the app is about the app, not the version: it carries over a new lock
    # (each is keyed by the difference itself, so one that no longer occurs is never matched). A
    # review was of the old version and does not.
    before = read_lock(locked.page)
    if before and isinstance(before.get("kept"), dict):
        record["kept"] = before["kept"]
    where.parent.mkdir(parents=True, exist_ok=True)
    _write(where, json.dumps(record, ensure_ascii=False, indent=1))
    _write(handoff, handoff_markdown(locked, record))
    return record


def _shown_folder(shown: str, page: Path, folder: Path) -> str:
    """The design folder as the page's shown path spells it: `design/` for `design/pricing.html`."""
    inside = page.relative_to(folder).as_posix()
    if shown.endswith(inside):
        return shown[: len(shown) - len(inside)]
    return str(folder) + os.sep


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def lock_summary(locked: Locked) -> dict[str, Any]:
    """What the lock dialog shows of a measurement, as short values. Pure."""
    widest = locked.system[0] if locked.system else {}
    colors = widest.get("colors") or {}
    seen: list[str] = []
    for role in ("background", "text", "border"):
        for entry in colors.get(role) or []:
            color = str(entry.get("key") or "")
            if color and color not in seen:
                seen.append(color)
    unnamed = [color for color in seen if locked.tokens.path is not None and not locked.tokens.names_color(color)]
    families: list[str] = []
    sizes: set[float] = set()
    for entry in widest.get("type") or []:
        family = str(entry.get("family") or "")
        if family and family not in families:
            families.append(family)
        sizes.add(float(entry.get("size") or 0))
    parts = [
        str(part.get("label") or part.get("heading") or part.get("tag") or "")
        for part in widest.get("sections") or []
    ]
    interactions = list(locked.report.behaviour.interactions)
    return {
        "page": locked.shown,
        "widths": list(locked.report.widths),
        "colors": seen[:12],
        "colors_count": len(seen),
        "colors_unnamed": unnamed[:8],
        "design_md": locked.report.design_md,
        "fonts": families[:4],
        "sizes": sorted(size for size in sizes if size)[::-1][:8],
        "spacing": [entry["px"] for entry in sorted(widest.get("spacing") or [], key=lambda e: -e["count"])[:6]],
        "parts": [part for part in parts if part][:12],
        "parts_count": len(parts),
        "states": len(interactions),
        "opens": sum(1 for entry in interactions if entry.get("opened")),
        "motion": [f"{entry.get('what')} {entry.get('ms')} ms" for entry in (widest.get("motion") or [])[:4]],
        "accepted": accepted_lines(locked.report),
        "app": app_summary(locked.app, seen, families),
    }
