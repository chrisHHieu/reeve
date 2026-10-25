"""The blocks a visual is made of: each one checked, its file read, and put in the shape the GUI
draws.

A block the model sends is checked against the catalog here, before anything is shown: an unknown
type, a missing field or a formula outside the grammar comes back to the model as the reason, the
way a tool refuses a bad argument. A block that names a file is filled from the file. What leaves
this module is the normalized block (it rides the presentation card) and one line saying what was
shown (it is what the model reads back).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .data import AGGREGATES, DataError, aggregate, column_of, parse_number, read_rows
from .formula import FormulaError, check_formula
from .places import PlaceError, iso_date, map_block, timeline

TYPES = ("chart", "table", "metrics", "compare", "what_if", "form", "timeline", "map")
CHART_KINDS = ("bar", "line", "area", "pie")
FIELD_KINDS = ("select", "radio", "checkbox", "text", "number", "textarea")

MAX_BLOCKS = 6
MAX_SERIES = 8
MAX_POINTS = 400
MAX_TABLE_ROWS = 500
MAX_ITEMS = 12

#: Opens a path the model named, inside the session's directories, or raises DataError.
Opener = Callable[[str], Path]


class BlockError(ValueError):
    pass


def _text(block: dict[str, Any], key: str, *, required: bool = False, limit: int = 200) -> str | None:
    value = block.get(key)
    if value is None or value == "":
        if required:
            raise BlockError(f"`{key}` is required")
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise BlockError(f"`{key}` must be text")
    return str(value)[:limit]


def _list(block: dict[str, Any], key: str, *, required: bool = True, most: int = 50) -> list[Any]:
    value = block.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise BlockError(f"`{key}` must be a non-empty list")
    if len(value) > most:
        raise BlockError(f"`{key}` has {len(value)} entries; at most {most}")
    return value


def _number(value: Any, what: str) -> float:
    number = parse_number(value)
    if number is None:
        raise BlockError(f"{what} must be a number, got {value!r}")
    return number


def _source(block: dict[str, Any], opener: Opener) -> tuple[str, list[str], list[dict[str, Any]]]:
    source = block.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("file"), str):
        raise BlockError("`source` must be an object with `file`")
    sheet = source.get("sheet")
    try:
        columns, rows = read_rows(opener(source["file"]), sheet=str(sheet) if sheet not in (None, "") else None)
    except DataError as exc:
        raise BlockError(str(exc)) from None
    return source["file"], columns, rows


_MONTH_YEAR = re.compile(r"^(?:T|Th[aá]ng\s*)?(\d{1,2})[/.-](\d{4})$", re.IGNORECASE)


def _time_keys(labels: list[str]) -> list[tuple[float, ...]] | None:
    """A sort key per label when every label is a point in time (`2026-07`, `9/10/2026`, `07/2026`,
    `2026`), so the labels can be put in time order; None when any one is not."""
    keys: list[tuple[float, ...]] = []
    for label in labels:
        text = label.strip()
        if match := _MONTH_YEAR.match(text):
            keys.append((float(match[2]), float(match[1]), 1.0))
        elif day := iso_date(text):
            keys.append(tuple(float(part) for part in day.split("-")))
        else:
            return None
    return keys if keys else None


def _chart(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    kind = block.get("kind", "bar")
    if kind not in CHART_KINDS:
        raise BlockError(f"`kind` is one of {', '.join(CHART_KINDS)}")
    out: dict[str, Any] = {"type": "chart", "kind": kind}
    for key in ("title", "unit", "x_label", "y_label"):
        if (value := _text(block, key)) is not None:
            out[key] = value
    if "source" in block:
        name, columns, rows = _source(block, opener)
        source = block["source"]
        how = source.get("agg", "sum")
        if how not in AGGREGATES:
            raise BlockError(f"`source.agg` is one of {', '.join(AGGREGATES)}")
        x = column_of(columns, source["x"], "source.x") if source.get("x") else None
        wanted = source.get("y", [])
        wanted = [wanted] if isinstance(wanted, str) else wanted
        if how != "count" and not wanted:
            raise BlockError("`source.y` names the column(s) to plot")
        try:
            ys = [column_of(columns, y, "source.y") for y in wanted][:MAX_SERIES]
            labels, values = aggregate(rows, x, ys, how)
        except DataError as exc:
            raise BlockError(str(exc)) from None
        names = ys if how != "count" else ["count"]
        order = list(range(len(labels)))
        # Bars of one series read best ranked. Bars over time (months, dates, years) read in time
        # order, and several series side by side have no one series to rank by: ranking months by
        # their first column put July, September, August, October in a row (a real session,
        # 2026-10-10). A `sort` the model names still wins.
        timed = _time_keys(labels)
        ranked = kind in ("bar", "pie") and how != "none" and len(names) == 1 and timed is None
        sort = source.get("sort", "desc" if ranked else "none")
        if sort in ("asc", "desc") and values:
            order.sort(key=lambda i: values[0][i], reverse=sort == "desc")
        elif timed is not None and how != "none":
            order.sort(key=lambda i: timed[i])
        limit = int(source.get("limit") or (30 if kind in ("bar", "pie") else MAX_POINTS))
        order = order[: max(1, min(limit, MAX_POINTS))]
        out["labels"] = [labels[i] for i in order]
        out["series"] = [{"name": n, "values": [round(v[i], 6) for i in order]} for n, v in zip(names, values)]
        out["source"] = {"file": name, "rows": len(rows)}
        shown_from = f" from {name} ({len(rows)} rows)"
    else:
        labels = [str(label)[:80] for label in _list(block, "labels", most=MAX_POINTS)]
        series = []
        for entry in _list(block, "series", most=MAX_SERIES):
            if not isinstance(entry, dict):
                raise BlockError("each `series` entry is {name, values}")
            values = [_number(v, "a series value") for v in _list(entry, "values", most=MAX_POINTS)]
            if len(values) != len(labels):
                raise BlockError(f"series {entry.get('name')!r} has {len(values)} values for {len(labels)} labels")
            series.append({"name": str(entry.get("name") or f"series {len(series) + 1}")[:80], "values": values})
        out["labels"], out["series"] = labels, series
        shown_from = ""
    if kind == "pie" and len(out["series"]) != 1:
        raise BlockError("a pie chart has exactly one series")
    head = out["series"][0]["values"]
    pairs = ", ".join(f"{label}={value:g}" for label, value in list(zip(out["labels"], head))[:12])
    more = f", … {len(head) - 12} more" if len(head) > 12 else ""
    title = out.get("title", "")
    return out, f"{kind} chart {title!r}{shown_from}: {pairs}{more}"


def _table(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    out: dict[str, Any] = {"type": "table"}
    if (title := _text(block, "title")) is not None:
        out["title"] = title
    if "source" in block:
        name, columns, rows = _source(block, opener)
        wanted = block["source"].get("columns")
        try:
            chosen = [column_of(columns, c, "source.columns") for c in wanted] if wanted else columns[:20]
        except DataError as exc:
            raise BlockError(str(exc)) from None
        out["columns"] = chosen
        out["rows"] = _fit([[str(row.get(c, ""))[:200] for c in chosen] for row in rows[:MAX_TABLE_ROWS]])
        out["source"] = {"file": name, "rows": len(rows)}
        return out, f"table {out.get('title', '')!r} from {name}: {len(rows)} rows, columns {', '.join(chosen)}"
    columns = [str(c)[:80] for c in _list(block, "columns", most=20)]
    rows = []
    for row in _list(block, "rows", most=MAX_TABLE_ROWS):
        if not isinstance(row, list) or len(row) != len(columns):
            raise BlockError(f"each row is a list of {len(columns)} cells")
        rows.append([str(cell)[:200] for cell in row])
    out["columns"], out["rows"] = columns, _fit(rows)
    return out, f"table {out.get('title', '')!r}: {len(rows)} rows"


#: The characters a table's cells may hold on the card: the card travels on the websocket frame of
#: its event, and a card past the frame budget (`cards.slim`) would not be drawn at all.
TABLE_CHARS = 60_000


def _fit(rows: list[list[str]]) -> list[list[str]]:
    """The first rows that fit the card; the source line still says how many the file has."""
    total = 0
    for index, row in enumerate(rows):
        total += sum(len(cell) for cell in row) + len(row)
        if total > TABLE_CHARS:
            return rows[:index]
    return rows


def _metrics(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    items = []
    for entry in _list(block, "items", most=MAX_ITEMS):
        if not isinstance(entry, dict):
            raise BlockError("each metric is {label, value}")
        item: dict[str, Any] = {"label": _text(entry, "label", required=True)}
        if "source" in entry:
            name, columns, rows = _source(entry, opener)
            source = entry["source"]
            how = source.get("agg", "sum")
            try:
                named = source.get("column", source.get("y"))
                column = column_of(columns, named, "source.column") if how != "count" else None
            except DataError as exc:
                raise BlockError(str(exc)) from None
            numbers = [n for n in (parse_number(row.get(column)) for row in rows) if n is not None] if column else []
            if how == "count":
                value = float(len(rows))
            elif not numbers:
                raise BlockError(f"column {column!r} in {name} has no numbers")
            else:
                value = {"sum": sum(numbers), "mean": sum(numbers) / len(numbers), "min": min(numbers),
                         "max": max(numbers)}.get(how)
                if value is None:
                    raise BlockError("a metric's `source.agg` is sum, count, mean, min or max")
            item["value"] = round(value, 6)
            item["source"] = name
        else:
            value = entry.get("value")
            if value is None or isinstance(value, (dict, list, bool)):
                raise BlockError("a metric needs `value` (or `source`)")
            item["value"] = value if isinstance(value, (int, float)) else str(value)[:40]
        for key in ("unit", "delta", "note"):
            if (text := _text(entry, key, limit=80)) is not None:
                item[key] = text
        items.append(item)
    out = {"type": "metrics", "items": items}
    return out, "metrics: " + ", ".join(f"{i['label']}={i['value']}" for i in items)


def _compare(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    options = []
    for entry in _list(block, "options", most=4):
        entry = {"name": entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict):
            raise BlockError("each option is {name, note?, highlight?}")
        option: dict[str, Any] = {"name": _text(entry, "name", required=True, limit=80)}
        if (note := _text(entry, "note")) is not None:
            option["note"] = note
        if entry.get("highlight") is True:
            option["highlight"] = True
        options.append(option)
    rows = []
    for entry in _list(block, "rows", most=30):
        values = entry.get("values") if isinstance(entry, dict) else None
        if not isinstance(values, list) or len(values) != len(options):
            raise BlockError(f"each row is {{label, values}} with {len(options)} values")
        rows.append({"label": _text(entry, "label", required=True, limit=80), "values": [str(v)[:120] for v in values]})
    out: dict[str, Any] = {"type": "compare", "options": options, "rows": rows}
    if (title := _text(block, "title")) is not None:
        out["title"] = title
    return out, f"comparison of {', '.join(o['name'] for o in options)} on {len(rows)} rows"


def _what_if(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    names: set[str] = set()
    inputs = []
    for entry in _list(block, "inputs", most=6):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").isidentifier():
            raise BlockError("each input is {name, label, min, max, step?, value}; `name` is an identifier")
        low, high = _number(entry.get("min"), "`min`"), _number(entry.get("max"), "`max`")
        if low >= high:
            raise BlockError(f"input {entry['name']!r}: `min` must be below `max`")
        value = _number(entry.get("value", low), "`value`")
        step = _number(entry.get("step", (high - low) / 100), "`step`")
        inputs.append({"name": entry["name"], "label": _text(entry, "label") or entry["name"], "min": low, "max": high,
                       "step": step if step > 0 else (high - low) / 100, "value": min(max(value, low), high),
                       **({"unit": u} if (u := _text(entry, "unit", limit=20)) else {})})
        names.add(entry["name"])

    def checked(formula: Any, where: str, extra: set[str] = frozenset()) -> Any:
        try:
            check_formula(formula, names | set(extra))
        except FormulaError as exc:
            raise BlockError(f"{where}: {exc}") from None
        return formula

    values = []
    for entry in _list(block, "values", required=False, most=12):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").isidentifier():
            raise BlockError("each of `values` is {name, formula}")
        values.append({"name": entry["name"], "formula": checked(entry.get("formula"), f"value {entry['name']!r}")})
        names.add(entry["name"])
    outputs = []
    # A what-if may be a chart and nothing else: refused for that in 1 of 6 live loan probes.
    for entry in _list(block, "outputs", required="chart" not in block, most=8):
        if not isinstance(entry, dict):
            raise BlockError("each output is {label, formula, unit?, decimals?}")
        output = {"label": _text(entry, "label", required=True, limit=80),
                  "formula": checked(entry.get("formula"), f"output {entry.get('label')!r}")}
        if (unit := _text(entry, "unit", limit=20)) is not None:
            output["unit"] = unit
        if isinstance(entry.get("decimals"), int) and 0 <= entry["decimals"] <= 6:
            output["decimals"] = entry["decimals"]
        outputs.append(output)
    out: dict[str, Any] = {"type": "what_if", "inputs": inputs, "values": values, "outputs": outputs}
    for key in ("title",):
        if (text := _text(block, key)) is not None:
            out[key] = text
    if block.get("highlight") in ("min", "max"):
        out["highlight"] = block["highlight"]
    chart = block.get("chart")
    if chart is not None:
        if not isinstance(chart, dict) or not isinstance(chart.get("x"), dict):
            raise BlockError("`chart` is {kind?, x: {name, from, to, step?, label?}, series: [{name, formula}]}")
        x = chart["x"]
        if not str(x.get("name") or "").isidentifier():
            raise BlockError("`chart.x.name` is an identifier")
        span = {"name": x["name"], "from": checked(x.get("from", 0), "chart.x.from"),
                "to": checked(x.get("to"), "chart.x.to"), "step": checked(x.get("step", 1), "chart.x.step")}
        if (label := _text(x, "label", limit=40)) is not None:
            span["label"] = label
        series = [{"name": _text(s, "name", required=True, limit=60) if isinstance(s, dict) else None,
                   "formula": checked(s.get("formula") if isinstance(s, dict) else None, "chart series", {x["name"], "x"})}
                  for s in _list(chart, "series", most=MAX_SERIES)]
        kind = chart.get("kind", "line")
        out["chart"] = {"kind": kind if kind in ("line", "area", "bar") else "line", "x": span, "series": series}
        if (unit := _text(chart, "unit", limit=20)) is not None:
            out["chart"]["unit"] = unit
    title = out.get("title", "")
    return out, f"what-if {title!r} with inputs {', '.join(i['name'] for i in inputs)}; outputs {', '.join(o['label'] for o in outputs)}"


def _form(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
    fields = []
    for entry in _list(block, "fields", most=12):
        if not isinstance(entry, dict):
            raise BlockError("each field is {name, label, kind, options?, value?}")
        kind = entry.get("kind", "text")
        if kind not in FIELD_KINDS:
            raise BlockError(f"a field's `kind` is one of {', '.join(FIELD_KINDS)}")
        field: dict[str, Any] = {"name": _text(entry, "name", required=True, limit=40),
                                 "label": _text(entry, "label") or entry.get("name"), "kind": kind}
        if kind in ("select", "radio", "checkbox"):
            field["options"] = [str(o)[:80] for o in _list(entry, "options", most=20)]
        if "value" in entry and not isinstance(entry["value"], dict):
            field["value"] = entry["value"] if isinstance(entry["value"], (list, bool, int, float)) else str(entry["value"])[:200]
        if (placeholder := _text(entry, "placeholder")) is not None:
            field["placeholder"] = placeholder
        if entry.get("required") is True:
            field["required"] = True
        fields.append(field)
    out: dict[str, Any] = {"type": "form", "fields": fields, "submit": _text(block, "submit", limit=40) or "Send"}
    if (title := _text(block, "title")) is not None:
        out["title"] = title
    return out, f"form {out.get('title', '')!r} asking {', '.join(f['label'] for f in fields)}; its answer arrives as the user's next message"


def _place(build: Callable[..., tuple[dict[str, Any], str]]) -> Callable[[dict[str, Any], Opener], tuple[dict[str, Any], str]]:
    def run(block: dict[str, Any], opener: Opener) -> tuple[dict[str, Any], str]:
        try:
            return build(block, lambda b: _source(b, opener))
        except PlaceError as exc:
            raise BlockError(str(exc)) from None

    return run


_BUILDERS = {"chart": _chart, "table": _table, "metrics": _metrics, "compare": _compare, "what_if": _what_if,
             "form": _form, "timeline": _place(timeline), "map": _place(map_block)}


def build_blocks(blocks: Any, opener: Opener) -> tuple[list[dict[str, Any]], list[str]]:
    """The normalized blocks and one line each on what was shown, or BlockError naming the block."""
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list) or not blocks:
        raise BlockError("`blocks` must be a non-empty list of blocks")
    if len(blocks) > MAX_BLOCKS:
        raise BlockError(f"at most {MAX_BLOCKS} blocks in one visual")
    built: list[dict[str, Any]] = []
    said: list[str] = []
    for index, block in enumerate(blocks, 1):
        kind = block.get("type") if isinstance(block, dict) else None
        if kind not in _BUILDERS:
            raise BlockError(f"block {index}: `type` is one of {', '.join(TYPES)}")
        try:
            out, line = _BUILDERS[kind](block, opener)
        except BlockError as exc:
            raise BlockError(f"block {index} ({kind}): {exc}") from None
        except (KeyError, TypeError, ValueError) as exc:
            raise BlockError(f"block {index} ({kind}): {exc}") from None
        built.append(out)
        said.append(line)
    return built, said


__all__ = ["BlockError", "MAX_BLOCKS", "TYPES", "build_blocks"]
