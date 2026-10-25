"""A visual as text, for a surface that draws none: the TUI and `reeve exec`.

ChatGPT keeps a Markdown fallback for every generated interface, for clients that cannot draw it
(Thesys's teardown, 2026-10). Here a placing line `:visual[v1]` on such a surface became "open
this session in the app to see it", which leaves a script or a terminal reader with nothing.
The card the harness built is the visual's data, so the text is written from it: a chart's
series and a table as a Markdown table, figures and a comparison as lines and a table, a
timeline as dated lines, a map as its countries and points, a what-if as its inputs, a form as
its questions. Nothing here is computed that the card does not hold: a what-if's outputs are
the GUI's to compute, so the text names them and says where to move them.
"""

from __future__ import annotations

import re
from typing import Any

#: The most rows a table or a chart is written with; the rest is counted, not dropped silently.
MAX_ROWS = 30


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(columns: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(_cell(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines.append(f"({len(rows) - MAX_ROWS} more rows)")
    return lines


def _with_unit(value: Any, unit: Any) -> str:
    text = _number(value)
    return f"{text}{unit}" if unit == "%" else f"{text} {unit}" if unit else text


def _block(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    title = block.get("title")
    head = [f"**{title}**"] if title else []
    if kind == "chart":
        series = block.get("series") or []
        unit = block.get("unit")
        rows = [[label, *(_with_unit(s["values"][i], unit) for s in series)] for i, label in enumerate(block.get("labels") or [])]
        return head + _table(["", *(s.get("name", "") for s in series)], rows)
    if kind == "table":
        return head + _table(block.get("columns") or [], block.get("rows") or [])
    if kind == "metrics":
        return [f"- {i['label']}: {_with_unit(i['value'], i.get('unit'))}" + (f" ({i['delta']})" if i.get("delta") else "")
                for i in block.get("items") or []]
    if kind == "compare":
        options = block.get("options") or []
        names = [o["name"] + (" ✓" if o.get("highlight") else "") for o in options]
        return head + _table(["", *names], [[r["label"], *r["values"]] for r in block.get("rows") or []])
    if kind == "timeline":
        return head + [f"- {i['start']}" + (f" → {i['end']}" if i.get("end") else "") + f": {i['label']}"
                       + (f" ({i['group']})" if i.get("group") else "") + (" ✓" if i.get("done") else "")
                       for i in block.get("items") or []]
    if kind == "map":
        unit = block.get("unit")
        regions = sorted(block.get("regions") or [], key=lambda r: -r["value"])
        lines = [f"- {r['name']}: {_with_unit(r['value'], unit)}" for r in regions[:MAX_ROWS]]
        lines += [f"- {p['label'] or 'point'} ({p['lat']:.2f}, {p['lon']:.2f})" + (f": {_with_unit(p['value'], unit)}" if "value" in p else "")
                  for p in (block.get("points") or [])[:MAX_ROWS]]
        return head + lines
    if kind == "what_if":
        inputs = [f"- {i['label']}: {_with_unit(i['value'], i.get('unit'))} (from {_number(i['min'])} to {_number(i['max'])})"
                  for i in block.get("inputs") or []]
        outputs = ", ".join(o["label"] for o in block.get("outputs") or [])
        return head + inputs + ([f"Computed in the app as you move them: {outputs}."] if outputs else [])
    if kind == "form":
        return head + [f"- {f['label']}" + (f": {' / '.join(f['options'])}" if f.get("options") else "")
                       for f in block.get("fields") or []] + ["Answer in a message."]
    return head


def visual_text(card: dict[str, Any]) -> str:
    """The visual a card holds, as Markdown a terminal or a script can read."""
    parts = ["\n".join(_block(b)) for b in card.get("blocks") or [] if isinstance(b, dict)]
    return "\n\n".join(p for p in parts if p)


_PLACED = re.compile(r"^[ \t]*:visual\[([\w-]+)\][ \t]*$", re.M)


def plain_visual_markers(text: str, shown: dict[str, dict[str, Any]] | None = None) -> str:
    """An answer for a surface that cannot draw a visual (the TUI, `reeve exec`): each placing
    line becomes the visual written as text when the surface saw its card, and a line saying
    what stood there when it did not."""
    shown = shown or {}

    def replace(match: re.Match[str]) -> str:
        card = shown.get(match.group(1))
        written = visual_text(card) if card else ""
        return written or f"[visual {match.group(1)}: open this session in the app to see it]"

    return _PLACED.sub(replace, text or "")
