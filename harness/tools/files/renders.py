"""What the model reads of a file tool's result.

Without a `render` the engine JSON-encodes the return value, which repeats a key name for every field
of every row and escapes every newline. These render the same facts as plain text, which is the
whole input-token win. The tool's return value, and the UI card built from `_card_meta`, are
unchanged.
"""

from __future__ import annotations

import json
from typing import Any


def _read_render(arguments: dict[str, Any], value: Any) -> str:
    """A read window as a metadata line plus verbatim source.

    The body carries no line-number gutter: edits match exact text, and a copied
    prefix is the usual cause of a failed edit. The header names the file, the
    window, and where to continue; exact line numbers come from `grep`.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    path = str(value.get("path") or arguments.get("path") or "")
    if isinstance(value.get("shown"), str):
        # A picture: the sentence is the answer, and the image rides beside it (`_attach`).
        return value["shown"]
    content = value.get("content")
    if not isinstance(content, str):
        return json.dumps(value, default=str)
    head = _read_head(path, value)
    body = "\n".join(row.partition("\t")[2] if "\t" in row else row for row in content.split("\n"))
    return f"{head}\n{body}"


def _read_head(path: str, value: dict[str, Any]) -> str:
    """The metadata line: file, window, and the `start_line` that continues it."""
    start, end, total = value.get("start_line"), value.get("end_line"), value.get("total_lines")
    if not (isinstance(start, int) and isinstance(end, int) and isinstance(total, int)):
        return path
    if total == 0:
        return f"{path} (empty file)"
    if start > total:
        # "lines 50-49 of 1" was the old answer to a start past the end (audit W10).
        return f"{path} (start_line {start} is past the end: the file has {total} line{'s' * (total != 1)})"
    encoding = value.get("encoding")
    head = f"{path} (lines {start}-{end} of {total}" + (f", read as {encoding}" if encoding else "")
    return f"{head}; next: start_line={end + 1})" if end < total else f"{head})"


def _listfiles_render(arguments: dict[str, Any], value: Any) -> str:
    """Paths joined by newlines, replacing a JSON array's quotes and commas — plus one
    line saying how many were left out, whenever any were (`list_files_tools`)."""
    if isinstance(value, dict) and value.get("error"):
        return f"Error: {value.get('error')}"
    found = value.get("files") if isinstance(value, dict) else value
    if not isinstance(found, list):
        return json.dumps(value, default=str)
    if not found:
        return "No files found"
    text = "\n".join(str(p) for p in found)
    if isinstance(value, dict) and value.get("truncated") is True:
        total = value.get("total")
        of = f" of {total:,}" if isinstance(total, int) and total > len(found) else ""
        order = value.get("order")
        text += (
            f"\n\n(showing {len(found)}{of} files"
            + (f", {order}" if isinstance(order, str) and order else "")
            + ": narrow the path or pattern to see the rest)"
        )
    return text


def _replace_render(arguments: dict[str, Any], value: Any) -> str:
    """A one-line edit confirmation, replacing the JSON result object."""
    if isinstance(value, dict) and value.get("error"):
        return f"Error: {value.get('error')}"
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    path = value.get("path") or arguments.get("path") or "the file"
    count = value.get("replacements")
    if isinstance(count, int):
        # `matched_via` is present only when a fallback rung matched (line numbers, escapes,
        # trailing whitespace). Saying so is the difference between a fuzzily-placed edit
        # the model can reason about and one it believes was exact.
        via = value.get("matched_via")
        suffix = f", {via}" if isinstance(via, str) and via else ""
        return (
            f"Updated {path} ({count} replacement{'' if count == 1 else 's'}{suffix})"
        )
    return f"Updated {path}"


def _patch_render(arguments: dict[str, Any], value: Any) -> str:
    """A one-line patch confirmation, replacing the JSON result object."""
    if isinstance(value, dict) and value.get("error"):
        return f"Error: {value.get('error')}"
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    files = value.get("file_count")
    if not isinstance(files, int):
        return "Applied patch"
    text = f"Applied patch: {files} file{'' if files == 1 else 's'}"
    hunks = value.get("hunk_count")
    if isinstance(hunks, int):
        text += f", {hunks} hunk{'' if hunks == 1 else 's'}"
    # As for `replace_in_file`: a hunk placed by a fallback rung says so.
    via = value.get("matched_via")
    return f"{text} ({via})" if isinstance(via, str) and via else text
