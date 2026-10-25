"""`design_guide` and `check_design`: the two design tools the model calls.

A design is made with the file tools like any other file, so there is no tool that "creates a
design". What the file tools cannot do is know how a design should be made here, and see the page
they wrote. `design_guide` is the first: how designs are made in Reeve, the project's DESIGN.md (or
how to draft one) and the craft rules. `check_design` is the second, on demand: the same check the
engine runs after every write of a design file, at the widths the model asks for, with the
screenshots attached.

Both are local only, like `present`: they read this machine's files and run this machine's browser.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root

from .. import cards
from ..contract import READ_ONLY_CAPS, ToolContract, ToolPresentation, attach_contract, clip
from ..files.access import escape_refusal
from .check import DEFAULT_WIDTHS, MAX_WIDTH, MAX_WIDTHS, MIN_WIDTH, check_page, clean_widths
from .guide import KIND_NAMES, TOPICS, guide_text
from .versions import say

#: A check opens a browser and loads the page up to four times; this is its ceiling.
CHECK_TIMEOUT_MS = 60_000

_GUIDE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "design_guide",
        "description": (
            "Read before designing a page, screen or UI: how designs are made in Reeve (one HTML "
            "page under design/, checked by Reeve after every write), this project's DESIGN.md or the "
            "directions to write one from (fonts, palettes, posture), the layout recipe for the kind of "
            "page, the file a page starts from, craft rules, and the last look before answering. Call "
            "it once per design task, before the first write."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(KIND_NAMES),
                    "description": "The kind of page, for its layout recipe.",
                },
                "topics": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(TOPICS)},
                    "description": "Only these rule sets; all of them when omitted.",
                },
            },
        },
    },
}

_CHECK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_design",
        "description": (
            "Open an HTML page in Reeve's own browser at several widths and measure it: parts that run "
            "off the window, text under its contrast, colors and fonts DESIGN.md does not name, "
            "controls too small or with no name, script errors. Returns what to fix and the "
            "screenshots. A page under design/ is already checked after every write; call this to "
            "check again, at other widths, or a page elsewhere."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The HTML file, relative to the workspace or absolute inside a session root.",
                },
                "widths": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        f"Window widths in CSS px, at most {MAX_WIDTHS}, {MIN_WIDTH}-{MAX_WIDTH}; "
                        f"default {', '.join(str(w) for w in DEFAULT_WIDTHS)}."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}


def design_tools(workspace: str, roots: Optional[list] = None, *, browser: Optional[Path] = None) -> list:
    """`design_guide` and `check_design` rooted at `workspace`. `roots` is the live list, resolved
    per call, so a folder granted mid-session can hold the next design checked."""
    root = Path(workspace).resolve()

    def design_guide(topics: Any = None, kind: Any = None) -> dict[str, Any]:
        wanted = [str(topic) for topic in topics] if isinstance(topics, list) else None
        chosen = str(kind) if isinstance(kind, str) and kind in KIND_NAMES else None
        return {"guide": guide_text(root, allowed_dirs(root, roots), wanted, chosen)}

    def check_design(path: Any = None, widths: Any = None) -> dict[str, Any]:
        shown = str(path or "").strip()
        if not shown:
            return {"error": "check_design needs `path`: the HTML file to check"}
        allowed = allowed_dirs(root, roots)
        target = (root / shown).resolve()
        home = containing_root(target, allowed)
        if home is None:
            return {"error": escape_refusal(allowed)}
        if not target.is_file():
            return {"error": f"Cannot check {shown}: file not found. Check the path and retry."}
        if target.suffix.lower() not in (".html", ".htm"):
            return {"error": f"Cannot check {shown}: only .html pages can be checked"}
        report = check_page(target, shown=shown, widths=clean_widths(widths), roots=allowed, browser=browser)
        out: dict[str, Any] = {
            "report": report.text(),
            "_facts": report.facts(),
            "_card_meta": {"design_check": design_card(report)},
        }
        parts = report.attach_parts()
        if parts:
            out["_attach"] = parts
        return out

    _bind(design_guide, _GUIDE_SCHEMA, GUIDE_CONTRACT)
    _bind(check_design, _CHECK_SCHEMA, CHECK_CONTRACT)
    return [design_guide, check_design]


def _bind(fn: Any, schema: dict[str, Any], contract: ToolContract) -> None:
    name = schema["function"]["name"]
    fn.__name__ = name
    fn.__doc__ = schema["function"]["description"]
    fn.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name=name, category="design", risk_level="low", capabilities=["design"], requires_approval=False
    )
    fn.__tool_schema__ = schema
    attach_contract(fn, contract)


def design_card(report: Any) -> dict[str, Any]:
    """What a surface draws for a check: one line, the findings under it, what this write changed
    (and what of that nobody pointed at), and a small picture for the list of versions."""
    lines = [line[2:] for line in report.text().splitlines()[1:] if line.startswith("- ")]
    card: dict[str, Any] = {"summary": summary_line(report), "lines": lines, "issues": report.issues, "path": report.shown}
    if report.changes:
        card["changes"] = say(report.changes)
    if report.unasked:
        card["unasked"] = say(report.unasked)
    if report.thumb:
        card["thumb"] = "data:image/jpeg;base64," + base64.b64encode(report.thumb).decode("ascii")
    return card


def summary_line(report: Any) -> str:
    """"Checked at 1440, 834 and 390 px: nothing to fix" — what the person reads on the row."""
    if report.error:
        said = f"Not opened: {report.error}"
        return said + (f"; its source: {report.issues} to fix" if report.issues else "")
    widths = [str(width) for width in report.widths]
    said = widths[0] if len(widths) == 1 else ", ".join(widths[:-1]) + " and " + widths[-1]
    verdict = "nothing to fix" if not report.issues else f"{report.issues} to fix"
    return f"Checked at {said} px: {verdict}"


# -- contracts ----------------------------------------------------------------------------


def _error_of(value: Any) -> str:
    return str(value.get("error") or "").strip() if isinstance(value, dict) else ""


def _guide_render(arguments: dict[str, Any], value: Any) -> str:
    if _error_of(value):
        return f"Error: {_error_of(value)}"
    return str(value.get("guide") or "") if isinstance(value, dict) else json.dumps(value, default=str)


def _guide_call(arguments: dict[str, Any]) -> ToolPresentation:
    return ToolPresentation(summary="Read the design guide", kind="read")


def _guide_result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    if _error_of(value):
        return ToolPresentation(summary=clip(_error_of(value)), kind="read")
    return ToolPresentation(summary="Read the design guide", kind="read")


def _check_render(arguments: dict[str, Any], value: Any) -> str:
    if _error_of(value):
        return f"Error: {_error_of(value)}"
    return str(value.get("report") or "") if isinstance(value, dict) else json.dumps(value, default=str)


def _check_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    facts = value.get("_facts") if isinstance(value, dict) else None
    return dict(facts) if isinstance(facts, dict) else {}


def _leaf(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path


def _check_call(arguments: dict[str, Any]) -> ToolPresentation:
    path = str((arguments or {}).get("path") or "").strip()
    summary = f"Check {_leaf(path)}" if path else "Check the design"
    return ToolPresentation(
        summary=summary,
        kind="read",
        card=cards.generic_call(summary, kind="read", locations=[{"path": path}] if path else None),
    )


def _check_result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    if _error_of(value):
        return ToolPresentation(summary=clip(_error_of(value)), kind="read")
    meta = card_meta.get("design_check") if isinstance(card_meta, dict) else None
    if not isinstance(meta, dict):
        return ToolPresentation(summary="Checked the design", kind="read")
    lines = [str(line) for line in meta.get("lines") or [] if isinstance(line, str)]
    summary = str(meta.get("summary") or "Checked the design")
    return ToolPresentation(
        summary=summary,
        kind="read",
        card=cards.generic_result(content=lines) if lines else None,
    )


GUIDE_CONTRACT = ToolContract(
    render=_guide_render,
    present_call=_guide_call,
    present_result=_guide_result,
    capabilities=READ_ONLY_CAPS,
)

CHECK_CONTRACT = ToolContract(
    render=_check_render,
    present_call=_check_call,
    present_result=_check_result,
    facts=_check_facts,
    timeout_ms=CHECK_TIMEOUT_MS,
    capabilities=READ_ONLY_CAPS,
)
