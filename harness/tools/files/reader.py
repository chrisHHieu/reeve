"""`read_file`: a windowed reader that tells the agent how to continue.

The aisuite toolkit's reader returns raw text, which the agent cannot cite as path:line without
counting, and raises outright on a large file, so the agent errors and guesses. This one windows a
big file and never fails on size, and it says how to keep reading. It is read-only and scoped to the
session's folders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import aisuite as ai
from harness.config.roots import allowed_dirs, containing_root
from harness.tools.office import is_office, office_text_lines

from ..contract import attach_contract
from ..path_hints import not_a_file
from ..text_sniff import sniff_text
from .access import escape_refusal
from .contracts import READ_FILE_CONTRACT
from .images import image_media, look_at, look_at_remote
from .schemas import READ_SCHEMA
from .window import (
    DEFAULT_MAX_LINES,
    MAX_TOTAL_CHARS,
    clip_line,
    is_pdf,
    pdf_text_lines,
    window_result,
    windowed_read_text,
)


def file_tools(workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None) -> list:
    """`read_file` rooted at `workspace`. With `roots` (a list of RootDir), absolute paths inside any
    root resolve too, since multi-root sessions (the universal scratch space) address their scratch
    and extra folders by the absolute paths the roots context advertises.

    `roots` is kept as the live list and re-resolved on every call (`allowed_dirs`), because
    `add_root` changes it in place when the person grants a folder mid-session. A copy taken at
    construction is how the file tools once ignored a grant the shell received in the same turn.

    `backend` (phase 2b) is the world's read backend. When it is present the file is read
    over the session's non-local world (SSH), the containment refusals are the world's own, and
    `workspace` and `roots` are not resolved on this machine at all."""
    root = Path(workspace).resolve()

    def read_remote(path: str, start: int, count: int) -> dict[str, Any]:
        shown = look_at_remote(backend, path)
        if shown is not None:
            return shown
        try:
            label = backend.display(path)
            whole = backend.read_file(path)
        except PermissionError as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except OSError as exc:
            return {"error": f"read failed: {exc}"}
        return windowed_read_text(whole, label, start, count)

    def read_file(path: str, start_line: int = 1, max_lines: int = DEFAULT_MAX_LINES) -> dict[str, Any]:
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        count = min(
            max_lines if isinstance(max_lines, int) and max_lines > 0 else DEFAULT_MAX_LINES,
            DEFAULT_MAX_LINES,
        )
        if backend is not None:
            return read_remote(path, start, count)
        target = (root / path).resolve()
        allowed = allowed_dirs(root, roots)
        home = containing_root(target, allowed)
        if home is None:  # reads stay inside the session's folders
            return {"error": escape_refusal(allowed)}
        if not target.is_file():
            return {"error": not_a_file(target, path)}
        label = str(target.relative_to(home)) if home == root else str(target)
        # A picture is shown, not read as text: the model gets a sentence and the image
        # attached beside it, as the separate `read_image` tool used to do.
        media = image_media(target)
        if media is not None:
            return look_at(target, label, media)

        selected: list[str] = []
        numbered: list[dict[str, Any]] = []
        encoding: Optional[str] = None
        total = used = 0
        budget_hit = False
        try:
            if is_pdf(target):
                lines, error = pdf_text_lines(target)
                if lines is None:
                    return {"error": error}
                source: "Iterable[Any]" = lines
            elif is_office(target):
                # A docx, xlsx, pptx or ipynb opened as text is replacement characters, and the
                # model would answer about a document it never read. It is extracted locally first
                # (harness/tools/office.py).
                lines, error = office_text_lines(target)
                if lines is None:
                    return {"error": error}
                source = lines
            else:
                encoding, refusal = sniff_text(target)
                if refusal:
                    return {"error": refusal}
                source = open(target, "r", encoding=encoding, errors="replace")
            try:
                for number, line in enumerate(source, 1):
                    total = number
                    if number < start or len(selected) >= count or budget_hit:
                        # Keep counting `total`, because the loop must reach the end either way so
                        # the continuation note knows the real size of the file.
                        continue
                    text = clip_line(str(line).rstrip("\n"))
                    entry = f"{number:>6}\t{text}"
                    if selected and used + len(entry) + 1 > MAX_TOTAL_CHARS:
                        # One line always gets through, so an oversized single line (already
                        # clipped above) can never return an empty window.
                        budget_hit = True
                        continue
                    used += len(entry) + 1
                    selected.append(entry)
                    numbered.append({"number": number, "text": text})
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    close()
        except OSError as exc:
            return {"error": f"read failed: {exc}"}

        result = window_result(label, start, total, selected, numbered)
        if encoding not in (None, "utf-8"):
            result["encoding"] = encoding
        return result

    read_file.__name__ = "read_file"
    read_file.__doc__ = READ_SCHEMA["function"]["description"]
    read_file.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="read_file",
        category="filesystem",
        risk_level="low",
        capabilities=["read"],
        requires_approval=False,
    )
    read_file.__tool_schema__ = READ_SCHEMA
    attach_contract(read_file, READ_FILE_CONTRACT)
    return [read_file]
