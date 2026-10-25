"""`show_visual` — a chart, a table, figures, a comparison, a calculator or a form, drawn under the
answer from a catalog the GUI owns.

The model names blocks; the harness checks each against the catalog and fills any block that
names a file from the file, so the numbers drawn are the data's. The normalized blocks ride the
result as `_card_meta` (the engine lifts it off before the model sees the value, the way `present`
carries its files), become the presentation card, and draw the same after a reload. The model reads
back one line per block: what was shown, with the figures a chart holds, so its prose can cite them.

Local only, like `present`: a block may read a file, and a remote session's files are on another
host.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root
from ..contract import READ_ONLY_CAPS, ToolContract, ToolPresentation, attach_contract, clip
from ..files.access import escape_refusal as _escapes
from .catalog import MAX_BLOCKS, TYPES, BlockError, build_blocks
from .data import DataError
from .text import plain_visual_markers, visual_text

#: When to show one, by the person's setting (Settings > Visuals): on the model's judgement, or
#: only when the person asks for something to look at. The probes (2026-10-09) measured each
#: sentence: "work numbers out first and show once" sent deepseek to write HTML pages instead
#: (two-loans 2/3 -> 0/3), so it is gone; the GUI folds a turn's drafts away instead.
_WHEN = {
    "auto": (
        "Use it when the answer is numbers to compare, a trend, a breakdown, a schedule, places, a "
        "what-if, or choices to collect. When one sentence decides the question and the person asked "
        "for nothing to look at or try, answer in that sentence and show nothing."
    ),
    "asked": (
        "Use it only when the person asks to see something: a chart, a table, a map, a timeline, a "
        "calculator to try, or a form. Otherwise answer in text."
    ),
}


def _description(mode: str = "auto") -> str:
    return (
        "Show the user a visual in your answer: a chart, a sortable table, key figures, a side-by-side "
        "comparison, a timeline, a map, a calculator with sliders, or a form. "
        + _WHEN.get(mode, _WHEN["auto"])
        # Two routes the probes showed taken wrong (2026-10-23, deepseek-v4.1-flash, 6 of 18 passed):
        # the model read the data file, computed with run_code and typed the figures it got, and a
        # calculator the person asked to try was built as an HTML page under design/. Said as "wants to
        # see or try", it also drew a calculator nobody asked for (decide-in-a-sentence 1 of 3): it is
        # said for what was asked.
        + " When the person asks for a calculator, chart or form to see or try, it goes here, in the "
        "answer, not in a page to design. When the figures come from a data file (.csv/.tsv/.json/.xlsx in the "
        "workspace; `sheet` picks a workbook's sheet), name it in `source` instead of typing them, even "
        "after you have read or computed them: the harness groups, sums and sorts the file's own rows "
        "(`x`, `y`, `agg`, `sort`, `limit`), so the visual cannot disagree with the data, and a typed "
        "copy can. Keep the file's unit and say it in `unit`; type values only when no file holds them. "
        "The visual is part of your final answer: you get back its name, place it there with a line "
        "`:visual[name]`, and do not repeat its figures or tables in text.\n"
        "Blocks (`type`):\n"
        "- chart: kind bar|line|area|pie, title, unit?; either labels + series [{name, values}] or "
        "source {file, sheet?, x, y (column or list), agg sum|count|mean|min|max|none, sort?, limit?}.\n"
        "- table: title?; columns + rows, or source {file, sheet?, columns?}.\n"
        "- metrics: items [{label, value | source {file, sheet?, column, agg}, unit?, delta?, note?}].\n"
        "- compare: title?, options [{name, note?, highlight?}], rows [{label, values (one per option)}].\n"
        "- timeline: title?; items [{label, start, end?, group?, note?, done?}] (dates 2026-10-09 or "
        "9/10/2026), or source {file, sheet?, label, start, end?, group?}.\n"
        "- map: title?, unit?; regions [{name (a country, any language, or ISO code), value}] and/or "
        "points [{lat, lon, label, value?}], or source {file, sheet?, region, value?, agg?} or "
        "{file, lat, lon, label?, value?}.\n"
        "- what_if: title?, inputs [{name, label, min, max, step?, value, unit?}], values? [{name, formula}], "
        "outputs [{label, formula, unit?, decimals?}], highlight? min|max, chart? {kind?, x {name, from, to, "
        "step?, label?}, series [{name, formula}]}. Formulas: + - * / % ** ( ), numbers, input/value names, "
        "min max abs round floor ceil sqrt pow log exp pmt(rate, periods, principal). The GUI recomputes "
        "as the user moves a slider; where they left it arrives with their next message. Two chart "
        "series are marked where they cross.\n"
        "- form: title?, fields [{name, label, kind select|radio|checkbox|text|number|textarea, options?, "
        "value?, required?}], submit. The answer arrives as the user's next message."
    )


_DESCRIPTION = _description()


def _schema(description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "show_visual",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "blocks": {
                        "type": "array",
                        "description": f"1 to {MAX_BLOCKS} blocks, drawn in order.",
                        "items": {
                            "type": "object",
                            "properties": {"type": {"type": "string", "enum": list(TYPES)}},
                            "required": ["type"],
                        },
                    },
                },
                "required": ["blocks"],
            },
        },
    }


def visual_tools(workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None, mode: str = "auto") -> list:
    """`show_visual` rooted at `workspace`, described for the person's setting (`auto`, `asked`;
    `off` is no tool); nothing for a remote world (its files are elsewhere)."""
    if backend is not None or mode == "off":
        return []
    description = _description(mode)
    root = Path(workspace).resolve()

    def opener(named: str) -> Path:
        target = (root / named).resolve()
        if containing_root(target, allowed_dirs(root, roots)) is None:
            raise DataError(_escapes(allowed_dirs(root, roots)))
        if not target.is_file():
            raise DataError(f"{named}: file not found")
        return target

    shown_count = 0

    def show_visual(blocks: Any = None) -> dict[str, Any]:
        nonlocal shown_count
        try:
            built, said = build_blocks(blocks, opener)
        except BlockError as exc:
            return {"error": f"Nothing was shown. {exc}"}
        # The name the answer places it by. Counted per session's tool, so a turn's visuals
        # are v1, v2, … in the order they were shown; a refused call takes no name.
        shown_count += 1
        name = f"v{shown_count}"
        return {"shown": said, "id": name, "_card_meta": {"visual": {"id": name, "blocks": built}}}

    show_visual.__name__ = "show_visual"
    show_visual.__doc__ = description
    show_visual.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="show_visual",
        category="filesystem",
        risk_level="low",
        capabilities=["present"],
        requires_approval=False,
    )
    show_visual.__tool_schema__ = _schema(description)
    attach_contract(show_visual, VISUAL_CONTRACT)
    return [show_visual]


def _render(arguments: dict[str, Any], value: Any) -> str:
    """What the model reads back: one line per block shown, or why nothing was."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    if value.get("error"):
        return f"Error: {value['error']}"
    lines = value.get("shown")
    if not isinstance(lines, list):
        return json.dumps(value, default=str)
    name = str(value.get("id") or "")
    if not name:
        return "Shown to the user:\n" + "\n".join(f"- {line}" for line in lines)
    # The person sees the visual inside the answer, so the answer is written around it: the
    # measured failure before this line was an answer that restated the visual as tables.
    return (
        f"Shown to the user as {name}:\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"\nIt is part of your answer: put `:visual[{name}]` on a line of its own where it "
        "belongs. Do not restate its figures or tables in text. Lead with the conclusion, then "
        "say what to look at in it and what it cannot show."
    )


def _title(blocks: list[Any]) -> str:
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("title"), str) and block["title"].strip():
            return block["title"].strip()
    kinds = [str(block.get("type")) for block in blocks if isinstance(block, dict)]
    return ", ".join(kinds) or "a visual"


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    blocks = arguments.get("blocks") if isinstance(arguments, dict) else None
    blocks = [blocks] if isinstance(blocks, dict) else blocks if isinstance(blocks, list) else []
    return ToolPresentation(summary=clip(_title(blocks)), kind="other")


def _present_result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    if isinstance(value, dict) and value.get("error"):
        return ToolPresentation(summary=clip(str(value["error"])), kind="other")
    visual = card_meta.get("visual") if isinstance(card_meta, dict) else None
    blocks = visual.get("blocks") if isinstance(visual, dict) else None
    if not isinstance(blocks, list):
        return ToolPresentation(summary="Showed a visual", kind="other")
    card: dict[str, Any] = {"card": "visual", "blocks": blocks}
    if isinstance(visual.get("id"), str):
        card["id"] = visual["id"]
    return ToolPresentation(summary=clip(_title(blocks)), kind="other", card=card)


def _facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the review counts: whether anything was shown, by what name, which blocks,
    and how many drew their numbers from a file. Placement is read from the answer itself."""
    if not isinstance(value, dict):
        return {}
    if value.get("error"):
        return {"shown": False}
    blocks = arguments.get("blocks") if isinstance(arguments, dict) else None
    blocks = [blocks] if isinstance(blocks, dict) else blocks if isinstance(blocks, list) else []
    kinds = [str(b.get("type")) for b in blocks if isinstance(b, dict)]
    sourced = sum(1 for b in blocks if isinstance(b, dict) and isinstance(b.get("source"), dict))
    return {"shown": True, "name": str(value.get("id") or ""), "blocks": ",".join(kinds), "sourced": sourced}


VISUAL_CONTRACT = ToolContract(
    render=_render,
    facts=_facts,
    present_call=_present_call,
    present_result=_present_result,
    timeout_ms=30_000,
    capabilities=READ_ONLY_CAPS,
)

__all__ = ["VISUAL_CONTRACT", "plain_visual_markers", "visual_text", "visual_tools"]
