"""Text extraction for the document formats a model cannot read natively.

A `.docx`, `.xlsx`, `.pptx` or `.ipynb` is a zip (or JSON) the model would otherwise
receive as replacement characters: `read_file` opens with `errors="replace"`, so the
model gets mojibake and may answer confidently about a document it never read. The
other harnesses extract locally before the model sees the file (one read tool
runs mammoth for docx and an xlsx reader; others only name the path). This
module is that extractor, stdlib-only — no new dependency, no packaging change.

What it is not: a renderer. Paragraphs, cell text and slide text in document order, with
truncation said out loud (a `…[truncated …]` line) rather than silently dropped.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

#: Suffixes this module extracts.
OFFICE_SUFFIXES = (".docx", ".xlsx", ".pptx", ".ipynb")

#: Hard cap on extracted characters. The read tool windows its output anyway; this only
#: stops a pathological document from being parsed into memory in full.
MAX_EXTRACT_CHARS = 2_000_000
#: Rows extracted per sheet before a truncation note.
MAX_SHEET_ROWS = 20_000

_SHEET_REF = re.compile(r"^([A-Z]+)")


def is_office(path: Path) -> bool:
    """Whether `path` is one of the formats this module can extract."""
    return path.suffix.lower() in OFFICE_SUFFIXES


def _local(tag: str) -> str:
    """The tag without its namespace: `{ns}w:p` -> `p`."""
    return tag.rsplit("}", 1)[-1]


def _runs_text(element: ElementTree.Element) -> str:
    """The concatenated `t` (text run) descendants, in document order."""
    return "".join(node.text or "" for node in element.iter() if _local(node.tag) == "t")


def _paragraphs(root: ElementTree.Element) -> list[str]:
    """Every paragraph's text, in document order — table cells included, because their
    paragraphs are `w:p`/`a:p` like any other."""
    return [
        _runs_text(node)
        for node in root.iter()
        if _local(node.tag) == "p"
    ]


def _docx_lines(zf: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(zf.read("word/document.xml"))
    return _paragraphs(root)


def _slide_number(name: str) -> int:
    match = re.search(r"(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _pptx_lines(zf: zipfile.ZipFile) -> list[str]:
    slides = sorted(
        (
            name
            for name in zf.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        ),
        key=_slide_number,
    )
    lines: list[str] = []
    for index, name in enumerate(slides, 1):
        lines.append(f"--- slide {index} ---")
        lines.extend(_paragraphs(ElementTree.fromstring(zf.read(name))))
    return lines


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [_runs_text(si) for si in root.iter() if _local(si.tag) == "si"]


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    """Sheet names in workbook order; member stems when the workbook part is absent."""
    try:
        root = ElementTree.fromstring(zf.read("xl/workbook.xml"))
    except KeyError:
        root = None
    names = []
    if root is not None:
        names = [
            node.get("name") or f"sheet{index}"
            for index, node in enumerate(
                (n for n in root.iter() if _local(n.tag) == "sheet"), 1
            )
        ]
    members = sorted(
        (
            name
            for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        ),
        key=_slide_number,
    )
    if not names:
        names = [Path(member).stem for member in members]
    return names


def _column_index(ref: Optional[str]) -> Optional[int]:
    """`"AB12"` -> 27 (zero-based column), so sparse rows keep their columns aligned."""
    if not ref:
        return None
    match = _SHEET_REF.match(ref.upper())
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return _runs_text(cell)
    value = next(
        (node.text or "" for node in cell.iter() if _local(node.tag) == "v"), ""
    )
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return value
    return value


def _sheet_cells(data: bytes, shared: list[str], dates: frozenset[int] = frozenset(), most: int = MAX_SHEET_ROWS) -> "tuple[list[list[str]], bool]":
    """The sheet's rows as cells, columns kept aligned across sparse rows, and whether it was
    cut at `most` rows. A cell whose style is in `dates` is written as an ISO date."""
    root = ElementTree.fromstring(data)
    rows: list[list[str]] = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        if len(rows) >= most:
            return rows, True
        cells: list[str] = []
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            column = _column_index(cell.get("r"))
            text = _cell_text(cell, shared)
            if dates and cell.get("t") in (None, "n") and cell.get("s", "").isdigit() and int(cell.get("s")) in dates:
                text = _serial_date(text)
            if column is not None:
                while len(cells) < column:
                    cells.append("")
            cells.append(text)
        rows.append(cells)
    return rows, False


def _sheet_rows(data: bytes, shared: list[str]) -> list[str]:
    rows, cut = _sheet_cells(data, shared)
    lines = ["\t".join(cells) for cells in rows]
    if cut:
        lines.append(f"…[truncated at row {MAX_SHEET_ROWS}]")
    return lines


#: Excel's built-in number formats that are dates or times.
_DATE_FORMATS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})


def _date_styles(zf: zipfile.ZipFile) -> frozenset[int]:
    """The cell styles (`s` indexes) whose number format is a date: a built-in date format, or a
    custom one whose code has day, month or year in it outside quotes and brackets."""
    try:
        root = ElementTree.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return frozenset()
    custom = set()
    for node in root.iter():
        if _local(node.tag) == "numFmt":
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", node.get("formatCode", "")).lower()
            if re.search(r"[dmy]", code) and node.get("numFmtId", "").isdigit():
                custom.add(int(node.get("numFmtId")))
    styles = set()
    xfs = next((node for node in root.iter() if _local(node.tag) == "cellXfs"), None)
    for index, xf in enumerate(xf for xf in (xfs if xfs is not None else []) if _local(xf.tag) == "xf"):
        number = xf.get("numFmtId", "0")
        if number.isdigit() and (int(number) in _DATE_FORMATS or int(number) in custom):
            styles.add(index)
    return frozenset(styles)


def _serial_date(text: str) -> str:
    """Excel's day count (1900 system) as `2026-10-09`, with the time when it has one."""
    import datetime

    try:
        moment = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(text))
    except (ValueError, OverflowError):
        return text
    return moment.strftime("%Y-%m-%d %H:%M" if moment.time() != datetime.time() else "%Y-%m-%d")


def xlsx_tables(data: bytes, most: int = MAX_SHEET_ROWS) -> "list[tuple[str, list[list[str]]]]":
    """Each sheet of a workbook as `(name, rows of cells)`, dates as ISO dates: what a reader of
    the data needs, where `_xlsx_lines` is text for a person or a model to read."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _shared_strings(zf)
        names = _sheet_names(zf)
        dates = _date_styles(zf)
        members = sorted(
            (name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            key=_slide_number,
        )
        return [
            (names[index] if index < len(names) else Path(member).stem, _sheet_cells(zf.read(member), shared, dates, most)[0])
            for index, member in enumerate(members)
        ]


def _xlsx_lines(zf: zipfile.ZipFile) -> list[str]:
    shared = _shared_strings(zf)
    names = _sheet_names(zf)
    members = sorted(
        (
            name
            for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        ),
        key=_slide_number,
    )
    lines: list[str] = []
    for index, member in enumerate(members):
        name = names[index] if index < len(names) else Path(member).stem
        lines.append(f"--- sheet: {name} ---")
        lines.extend(_sheet_rows(zf.read(member), shared))
    return lines


def _ipynb_lines(data: bytes) -> list[str]:
    """A notebook cell by cell, with numbers, ids and outputs (`tools/notebook.py`)."""
    from harness.tools.notebook import notebook_lines

    return notebook_lines(data)


def office_text_lines(path: Path) -> "tuple[Optional[list[str]], str]":
    """The document's text as lines, or `(None, error)`.

    Same contract as `files._pdf_text_lines`: the read tool windows and numbers whatever
    comes back, so this returns plain lines and a marker line per sheet/slide/cell group.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read failed: {exc}"
    return office_text_from_bytes(path.name, data)


def office_text_from_bytes(name: str, data: bytes) -> "tuple[Optional[list[str]], str]":
    """`office_text_lines` for a document already in memory (a chat attachment)."""
    path = Path(name)
    suffix = path.suffix.lower()
    try:
        if suffix == ".ipynb":
            lines = _ipynb_lines(data)
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if suffix == ".docx":
                    lines = _docx_lines(zf)
                elif suffix == ".pptx":
                    lines = _pptx_lines(zf)
                else:
                    lines = _xlsx_lines(zf)
    except (zipfile.BadZipFile, ElementTree.ParseError, json.JSONDecodeError) as exc:
        return None, f"could not extract text from {path.name}: {exc}"
    except KeyError as exc:
        return None, f"could not extract text from {path.name}: missing part {exc}"

    if not any(line.strip() for line in lines):
        return ["(this document contains no extractable text)"], ""

    capped: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > MAX_EXTRACT_CHARS:
            capped.append(f"…[truncated at {MAX_EXTRACT_CHARS:,} characters]")
            break
        capped.append(line)
        used += len(line) + 1
    return capped, ""
