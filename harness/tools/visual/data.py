"""Rows from a file a visual names, and the series computed from them.

A chart that names `reports/q3.csv` is drawn from what the harness reads there, not from numbers
the model typed: the harness opens the file, groups and sums, and the card keeps the result. CSV
(delimiter sniffed), TSV, JSON (a list of objects, or `{"rows": [...]}`) and Excel workbooks
(`.xlsx`: a sheet by name or the first with data, its first row the header, date cells as ISO
dates) are read; anything else is refused with the reason, so the model can export the data to
one of those first. Office work lives in workbooks: a visual that could not read one sent the
model to convert it with a script first.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Optional

#: The largest file a visual reads, and the most rows it keeps from one.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000

AGGREGATES = ("sum", "count", "mean", "min", "max", "none")


class DataError(ValueError):
    pass


def parse_number(value: Any) -> Optional[float]:
    """A number as tables write one (`6.500.000đ`, `1,250,000`, `12%`, `-3,5`); the GUI's
    `tableModel.parseNumber` reads the same way."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("−", "-")
    text = re.sub(r"[\s ]", "", text)
    if not text:
        return None
    text = re.sub(r"^[$€£¥₫]", "", text)
    text = re.sub(r"(%|₫|đ|vnd|usd|eur|triệu|tr|tỷ|k|m|bn|b|x|×|ms|s|kb|mb|gb)$", "", text, flags=re.I)
    if not re.fullmatch(r"[+-]?[\d.,]*\d[\d.,]*", text):
        return None
    sign = -1.0 if text.startswith("-") else 1.0
    text = text.lstrip("+-")
    grouped = re.fullmatch(r"\d{1,3}([.,])\d{3}(?:\1\d{3})*(?:([.,])(\d+))?", text)
    if grouped:
        group, decimal, frac = grouped.group(1), grouped.group(2), grouped.group(3)
        if decimal and decimal == group:
            return None
        whole = (text.split(decimal)[0] if decimal else text).replace(group, "")
        return sign * float(f"{whole}.{frac}" if frac else whole)
    if re.fullmatch(r"\d+,\d+", text):
        return sign * float(text.replace(",", "."))
    if re.fullmatch(r"\d*\.?\d+", text):
        return sign * float(text)
    return None


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _workbook_rows(path: Path, sheet: Optional[str]) -> tuple[list[str], list[dict[str, Any]]]:
    import zipfile
    from xml.etree import ElementTree

    from ..office import xlsx_tables

    try:
        sheets = xlsx_tables(path.read_bytes(), most=MAX_ROWS + 1)
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise DataError(f"{path.name} is not a workbook this can read: {exc}") from None
    if sheet is not None:
        found = [cells for name, cells in sheets if name.strip().lower() == str(sheet).strip().lower()]
        if not found:
            raise DataError(f"{path.name} has no sheet {sheet!r}; its sheets: {', '.join(n for n, _ in sheets)}")
        cells = found[0]
    else:
        cells = next((cells for _, cells in sheets if any(any(c.strip() for c in row) for row in cells)), [])
    cells = [row for row in cells if any(c.strip() for c in row)]
    if not cells:
        raise DataError(f"{path.name}: the sheet is empty")
    header, body = cells[0], cells[1:MAX_ROWS + 1]
    width = max(len(row) for row in cells)
    columns = [(header[i].strip() if i < len(header) else "") or f"column {i + 1}" for i in range(width)]
    return columns, [{columns[i]: row[i] if i < len(row) else "" for i in range(width)} for row in body]


def read_rows(path: Path, sheet: Optional[str] = None) -> tuple[list[str], list[dict[str, Any]]]:
    """The file's columns and rows (each a dict by column), or DataError saying why not."""
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".tsv", ".txt", ".json", ".xlsx"):
        raise DataError(
            f"{path.name}: a visual reads .csv, .tsv, .json or .xlsx; export the data to one of those first"
        )
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise DataError(f"{path.name} is {size // (1024 * 1024)} MB; a visual reads files up to 25 MB")
    if suffix == ".xlsx":
        return _workbook_rows(path, sheet)
    text = _decode(path.read_bytes())
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataError(f"{path.name} is not valid JSON: {exc.msg} at line {exc.lineno}") from None
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            data = data["rows"]
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise DataError(f"{path.name}: expected a list of objects (or {{\"rows\": [...]}})")
        columns: list[str] = []
        for row in data[:MAX_ROWS]:
            for key in row:
                if key not in columns:
                    columns.append(str(key))
        return columns, [dict(row) for row in data[:MAX_ROWS]]
    sample = text[:20_000]
    delimiter = "\t" if suffix == ".tsv" else None
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        raise DataError(f"{path.name} is empty")
    columns = [name.strip() or f"column {i + 1}" for i, name in enumerate(header)]
    rows = []
    for record in reader:
        if not any(cell.strip() for cell in record):
            continue
        rows.append({columns[i]: record[i] if i < len(record) else "" for i in range(len(columns))})
        if len(rows) >= MAX_ROWS:
            break
    return columns, rows


def column_of(columns: list[str], wanted: Any, what: str) -> str:
    """The file's column named `wanted` (case- and space-insensitive), or DataError listing them."""
    if not isinstance(wanted, str) or not wanted.strip():
        raise DataError(f"`{what}` must name a column")
    folded = {re.sub(r"\s+", " ", c).strip().lower(): c for c in columns}
    found = folded.get(re.sub(r"\s+", " ", wanted).strip().lower())
    if found is None:
        raise DataError(f"no column {wanted!r} for `{what}`; the file has: {', '.join(columns[:30])}")
    return found


def aggregate(rows: list[dict[str, Any]], x: Optional[str], ys: list[str], how: str) -> tuple[list[str], list[list[float]]]:
    """Labels and one series per `ys` column: grouped by `x` and combined `how`, or row by row
    (`none`). `count` counts the rows of each group and needs no `ys`."""
    if how not in AGGREGATES:
        raise DataError(f"`agg` is one of {', '.join(AGGREGATES)}")
    if how == "none":
        labels = [str(row.get(x, "")) if x else str(i + 1) for i, row in enumerate(rows)]
        return labels, [[parse_number(row.get(y)) or 0.0 for row in rows] for y in ys]
    if x is None:
        raise DataError("grouping needs `x`, the column to group by")
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(x, "")).strip() or "(blank)"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    if how == "count":
        return order, [[float(len(groups[key])) for key in order]]
    series = []
    for y in ys:
        values = []
        for key in order:
            numbers = [n for n in (parse_number(row.get(y)) for row in groups[key]) if n is not None]
            if not numbers:
                values.append(0.0)
            elif how == "sum":
                values.append(sum(numbers))
            elif how == "mean":
                values.append(sum(numbers) / len(numbers))
            elif how == "min":
                values.append(min(numbers))
            else:
                values.append(max(numbers))
        series.append(values)
    return order, series


__all__ = ["AGGREGATES", "DataError", "MAX_FILE_BYTES", "MAX_ROWS", "aggregate", "column_of", "parse_number", "read_rows"]
