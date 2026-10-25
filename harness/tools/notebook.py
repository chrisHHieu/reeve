"""Jupyter notebooks, cell by cell: how `read_file` shows one and how `edit_notebook` changes one.

A notebook is JSON: every source line a quoted string with an escaped `\\n`, every image output a
base64 blob. Read as text it is noise and costly; edited as text, one wrong escape breaks the file.
So both sides work in cells.

`read_file` shows each cell under a header naming its number, kind and id, then its outputs as text:
a stream, a result's `text/plain`, an error's name, value and the end of its traceback. A picture
or an HTML output is named, not shown. `edit_notebook` addresses a cell by its id, so inserting one
never moves the cell a later call means; a notebook from before cell ids (nbformat < 4.5) is
addressed by number.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Optional

#: Output lines one cell shows before it says how many more there were.
MAX_OUTPUT_LINES = 30
#: Traceback lines kept from the end: the frame that raised is at the bottom.
MAX_TRACEBACK_LINES = 12
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _text(value: Any) -> str:
    """A notebook string field, which nbformat allows as one string or a list of lines."""
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    return str(value or "")


def _output_lines(output: dict[str, Any]) -> list[str]:
    kind = output.get("output_type")
    if kind == "stream":
        return _text(output.get("text")).splitlines()
    if kind == "error":
        trace = [_ANSI.sub("", str(line)) for line in output.get("traceback") or []]
        tail = "\n".join(trace).splitlines()[-MAX_TRACEBACK_LINES:]
        return [f"{output.get('ename', 'Error')}: {output.get('evalue', '')}", *tail]
    data = output.get("data") if isinstance(output.get("data"), dict) else {}
    if "text/plain" in data:
        lines = _text(data["text/plain"]).splitlines()
    else:
        lines = []
    shown = [media for media in data if media.startswith("image/")]
    if shown:
        lines.append(f"[{', '.join(shown)} output: not shown here]")
    elif not lines and data:
        lines.append(f"[{', '.join(sorted(data))} output]")
    return lines


def cell_header(number: int, cell: dict[str, Any]) -> str:
    parts = [f"cell {number}", str(cell.get("cell_type") or "cell")]
    if cell.get("id"):
        parts.append(f"id {cell['id']}")
    count = cell.get("execution_count")
    if isinstance(count, int):
        parts.append(f"ran {count}")
    return "--- " + " · ".join(parts) + " ---"


def notebook_lines(data: bytes) -> list[str]:
    """The notebook as the lines `read_file` windows: a header per cell, its source, its outputs."""
    payload: Any = json.loads(data.decode("utf-8", errors="replace"))
    lines: list[str] = []
    for number, cell in enumerate(payload.get("cells") or [], 1):
        if not isinstance(cell, dict):
            continue
        lines.append(cell_header(number, cell))
        lines.extend(_text(cell.get("source")).splitlines())
        outputs: list[str] = []
        for output in cell.get("outputs") or []:
            if isinstance(output, dict):
                outputs.extend(_output_lines(output))
        if outputs:
            lines.append("--- out ---")
            lines.extend(outputs[:MAX_OUTPUT_LINES])
            if len(outputs) > MAX_OUTPUT_LINES:
                lines.append(f"…[{len(outputs) - MAX_OUTPUT_LINES} more output lines]")
    return lines


# -- editing ----------------------------------------------------------------------------


class NotebookError(ValueError):
    """A notebook edit that cannot be made, in words the model can act on."""


def load(path: Path) -> "tuple[dict[str, Any], str]":
    """The notebook and the line ending its file uses."""
    raw = path.read_bytes().decode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotebookError(f"{path.name} is not valid notebook JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
        raise NotebookError(f"{path.name} has no cell list: not a Jupyter notebook")
    return payload, ("\r\n" if "\r\n" in raw else "\n")


def save(path: Path, payload: dict[str, Any], newline: str) -> None:
    """Write the notebook back the way Jupyter writes it (one-space indent, a final newline, the
    file's own line ending), whole or not at all."""
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    fd, tmp = tempfile.mkstemp(prefix=".nb-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def has_ids(payload: dict[str, Any]) -> bool:
    """nbformat 4.5 and later give every cell an id."""
    minor = payload.get("nbformat_minor")
    return payload.get("nbformat", 0) > 4 or (payload.get("nbformat") == 4 and isinstance(minor, int) and minor >= 5)


def find(payload: dict[str, Any], cell: str) -> int:
    """The index of the cell an edit names: its id, or its number (1-based) as `read_file` shows it."""
    cells = payload["cells"]
    wanted = str(cell or "").strip()
    for index, entry in enumerate(cells):
        if isinstance(entry, dict) and entry.get("id") == wanted:
            return index
    number = wanted.lstrip("#")
    if number.isdigit() and 1 <= int(number) <= len(cells):
        return int(number) - 1
    known = [str(c.get("id")) for c in cells if isinstance(c, dict) and c.get("id")]
    hint = f"; its cell ids: {', '.join(known[:12])}" if known else f"; it has {len(cells)} cells, numbered from 1"
    raise NotebookError(f"no cell {wanted!r} in this notebook{hint}")


def new_cell(cell_type: str, source: str, with_id: bool) -> dict[str, Any]:
    cell: dict[str, Any] = {"cell_type": cell_type, "metadata": {}, "source": _source(source)}
    if with_id:
        cell["id"] = secrets.token_hex(4)
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _source(text: str) -> list[str]:
    """A source string as nbformat stores it: lines that keep their newline, the last without."""
    return str(text).splitlines(keepends=True)


def set_source(cell: dict[str, Any], source: str, cell_type: Optional[str]) -> None:
    """Replace a cell's source. A code cell's outputs and count are cleared: they belong to the
    code that ran, not to the code now written."""
    if cell_type and cell_type != cell.get("cell_type"):
        cell["cell_type"] = cell_type
        if cell_type == "code":
            cell.setdefault("outputs", [])
            cell.setdefault("execution_count", None)
        else:
            cell.pop("outputs", None)
            cell.pop("execution_count", None)
    cell["source"] = _source(source)
    if cell.get("cell_type") == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
