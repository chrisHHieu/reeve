"""`edit_notebook`: change a Jupyter notebook one cell at a time.

`replace_in_file` on a notebook edits JSON: every line of source is a quoted string ending in an
escaped newline, so the text the model saw in `read_file` (cells, as code) is not the text in the
file, and one wrong escape leaves a notebook Jupyter cannot open. This tool takes the cell as code
and writes valid nbformat back.

A cell is named by its id (what `read_file` prints in each cell's header), so inserting a cell
never moves the one a later call means; a notebook from before cell ids is named by number. The
write is whole or nothing, keeps the file's own line ending, and is a write like any other to the
permission engine, the read-before-edit gate and the undo store (its `path` is located the same way
as `write_file`'s).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root

from . import notebook
from .contract import WORKSPACE_WRITE_CAPS, ToolContract, ToolPresentation, attach_contract, clip
from .files.access import escape_refusal

ACTIONS = ("replace", "insert", "delete")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_notebook",
        "description": (
            "Change one cell of a Jupyter notebook (.ipynb): replace its source, insert a new "
            "cell after it, or delete it. Write the cell as plain code or markdown, never as "
            "notebook JSON: the file is written back as valid nbformat. Name the cell by the id "
            "read_file shows in its header (`--- cell 3 · code · id 7f3a9c01 ---`), or by its "
            "number when the notebook has no ids. Read the notebook first. A code cell you "
            "change loses its old outputs; run the notebook to refresh them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The .ipynb file."},
                "cell": {
                    "type": "string",
                    "description": (
                        "The cell's id, or its number from read_file. For `insert`, the new "
                        "cell goes after this one; \"0\" puts it first."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "replace (default), insert or delete.",
                },
                "source": {
                    "type": "string",
                    "description": "The cell's new source, as code or markdown (replace, insert).",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown", "raw"],
                    "description": "The kind of cell: required for insert, and changes a cell's kind on replace.",
                },
            },
            "required": ["path", "cell"],
        },
    },
}


def _render(arguments: dict[str, Any], value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    if value.get("error"):
        return f"Error: {value['error']}"
    what = {"replace": "Replaced", "insert": "Inserted", "delete": "Deleted"}.get(str(value.get("action")), "Changed")
    cell = f"cell {value.get('cell')}"
    if value.get("id"):
        cell += f" (id {value['id']})"
    line = f"{what} {cell} in {value.get('path')}: {value.get('cells')} cells now."
    if value.get("cleared"):
        line += " Its old outputs were cleared; run the notebook to see new ones."
    return line


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    action = str(arguments.get("action") or "replace")
    verb = {"insert": "Insert a cell after", "delete": "Delete"}.get(action, "Edit")
    summary = f"{verb} cell {clip(arguments.get('cell'), 20)} in {clip(arguments.get('path'), 120)}"
    return ToolPresentation(summary=summary, detail=clip(arguments.get("source"), 400), kind="edit")


def _present_result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    if isinstance(value, dict) and value.get("error"):
        return ToolPresentation(summary=clip(value["error"]), kind="edit")
    return ToolPresentation(summary=_render(arguments, value), kind="edit")


CONTRACT = ToolContract(
    render=_render,
    present_call=_present_call,
    present_result=_present_result,
    timeout_ms=60_000,
    capabilities=WORKSPACE_WRITE_CAPS,
)


def notebook_tools(workspace: str, roots: Optional[list] = None) -> list:
    """`edit_notebook` over this machine's files, scoped to the session's roots like the writers."""
    root = Path(workspace).resolve()

    def edit_notebook(
        path: str,
        cell: str,
        action: str = "replace",
        source: str = "",
        cell_type: Optional[str] = None,
    ) -> dict[str, Any]:
        target = (root / path).resolve()
        allowed = allowed_dirs(root, roots)
        home = containing_root(target, allowed)
        if home is None:
            raise PermissionError(escape_refusal(allowed))
        if target.suffix.lower() != ".ipynb":
            return {"error": f"{path} is not a notebook (.ipynb); edit it with replace_in_file"}
        if not target.is_file():
            return {"error": f"file does not exist: {path}"}
        verb = str(action or "replace").strip().lower()
        if verb not in ACTIONS:
            return {"error": f"action must be one of {', '.join(ACTIONS)}"}
        kind = str(cell_type).strip().lower() if cell_type else None
        if kind and kind not in ("code", "markdown", "raw"):
            return {"error": "cell_type must be code, markdown or raw"}
        try:
            payload, newline = notebook.load(target)
            cells = payload["cells"]
            cleared = False
            if verb == "insert":
                if not kind:
                    return {"error": "insert needs cell_type (code, markdown or raw)"}
                at = 0 if str(cell).strip() in ("0", "#0") else notebook.find(payload, cell) + 1
                cells.insert(at, notebook.new_cell(kind, source, notebook.has_ids(payload)))
                index = at
            elif verb == "delete":
                index = notebook.find(payload, cell)
                gone = cells.pop(index)
                notebook.save(target, payload, newline)
                return {
                    "path": _label(target, home, root), "action": verb, "cell": index + 1,
                    "id": gone.get("id") if isinstance(gone, dict) else None, "cells": len(cells),
                }
            else:
                index = notebook.find(payload, cell)
                cleared = bool(cells[index].get("outputs"))
                notebook.set_source(cells[index], source, kind)
            notebook.save(target, payload, newline)
        except notebook.NotebookError as exc:
            return {"error": str(exc)}
        except (OSError, UnicodeDecodeError) as exc:
            return {"error": f"could not edit the notebook: {exc}"}
        changed = cells[index]
        return {
            "path": _label(target, home, root),
            "action": verb,
            "cell": index + 1,
            "id": changed.get("id"),
            "type": changed.get("cell_type"),
            "cells": len(cells),
            **({"cleared": True} if cleared else {}),
        }

    edit_notebook.__name__ = "edit_notebook"
    edit_notebook.__doc__ = SCHEMA["function"]["description"]
    edit_notebook.__tool_schema__ = SCHEMA
    edit_notebook.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="edit_notebook",
        category="filesystem",
        risk_level="medium",
        capabilities=["edit_file"],
        requires_approval=True,
    )
    attach_contract(edit_notebook, CONTRACT)
    return [edit_notebook]


def _label(target: Path, home: Path, root: Path) -> str:
    return str(target.relative_to(home)) if home == root else str(target)
