"""Fast code search (`grep`): the ripgrep an install carries, else the one on PATH, else a Python
walk.

ripgrep respects `.gitignore`, so it skips `node_modules`, `target` and `dist` by itself. The
fallback skips a fixed set of heavy folders (`ignore.py`). The tool is read-only and scoped to the
workspace, and it returns `file:line:text`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root

from ..contract import READ_ONLY_CAPS, ToolContract, attach_contract
from ..files.access import escape_refusal as _escapes
from ..grep_engines import (  # noqa: F401  (_parse_rg is re-exported for its tests)
    _DEFAULT_MAX_RESULTS,
    _MAX_RESULTS,
    CONTENT,
    MAX_CONTEXT,
    MODES,
    _parse_rg,
    carried_rg,
    py_search,
    rg_search,
    type_globs,
)
from ..grep_present import _grep_facts, _grep_present_call, _grep_present_result, _grep_render  # noqa: F401
from . import ignore
from .ignore import OS_DATA_DIRS  # noqa: F401
from .remote import remote_grep
from .schema import SCHEMA

GREP_CONTRACT = ToolContract(
    capabilities=READ_ONLY_CAPS,
    render=_grep_render,
    present_call=_grep_present_call,
    present_result=_grep_present_result,
    facts=_grep_facts,
    timeout_ms=120_000,
)


def search_tools(workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None) -> list:
    """`grep` over this machine, or over the session's remote storage when `backend` is its remote
    world (phase 2b)."""
    root = Path(workspace).resolve()

    def grep(
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        max_results: int = _DEFAULT_MAX_RESULTS,
        output_mode: str = CONTENT,
        context: int = 0,
        type: Optional[str] = None,
        ignore_case: bool = False,
        multiline: bool = False,
        before: Optional[int] = None,
        after: Optional[int] = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        mode = output_mode if output_mode in MODES else CONTENT
        lines = _lines(context) if mode == CONTENT else 0
        lines_before = _lines(before, lines) if mode == CONTENT else 0
        lines_after = _lines(after, lines) if mode == CONTENT else 0
        limit = min(
            max_results if isinstance(max_results, int) and max_results > 0 else _DEFAULT_MAX_RESULTS,
            _MAX_RESULTS,
        )
        start = offset if isinstance(offset, int) and offset > 0 else 0
        types, unknown = type_globs(type)
        if unknown:
            return {"error": unknown}
        options = {
            "offset": start,
            "ignore_case": ignore_case is True,
            "multiline": multiline is True,
            "types": types,
        }
        if backend is not None:
            return remote_grep(
                backend, pattern, path, glob, limit, mode, before=lines_before, after=lines_after, **options
            )
        base = (root / (path or ".")).resolve()
        # Any folder the session holds is searchable, and not only the workspace anchor. The roots
        # list is the live one the permission engine reads too, and a folder an allowed call
        # reached was added to it before this runs. `grep` used to know only
        # `workspace`, so a folder granted mid-session answered "path escapes the workspace" while
        # `read_file` in the same `run_code` program read a file there.
        allowed = allowed_dirs(root, roots)
        if containing_root(base, allowed) is None:
            # The same sentence as every other file tool, remedy included (files._escapes).
            return {"error": _escapes(allowed)}
        if not base.exists():
            # ripgrep answers a missing path with "IO error for operation on …", which names no
            # cause; a model that joined three folders with `|` got that twice and read it as a
            # broken tool (2026-10-15). Say what is wrong and what `path` takes.
            return {"error": _missing_path(path or ".")}

        rg = carried_rg() or shutil.which("rg")
        if rg:
            try:
                found = rg_search(
                    rg,
                    root=root,
                    base=base,
                    pattern=pattern,
                    glob=glob,
                    n=limit,
                    mode=mode,
                    context=lines,
                    before=lines_before,
                    after=lines_after,
                    # Generated and dependency folders are skipped even without a .gitignore.
                    # `ignore_globs` leaves out any name the search root itself lives under.
                    exclude_globs=ignore.ignore_globs(base),
                    **options,
                )
            except Exception as exc:
                if "--multiline" in str(exc):
                    # ripgrep refuses a pattern that can only match across a line break unless
                    # it is told to search that way; say which switch, not ripgrep's flag.
                    return {"error": "this pattern matches across lines: call again with multiline=true"}
                return {"error": f"grep failed: {exc}"}
            return {"engine": "ripgrep", "mode": mode, **found}

        found = py_search(
            root=root,
            base=base,
            pattern=pattern,
            glob=glob,
            n=limit,
            mode=mode,
            context=lines,
            before=lines_before,
            after=lines_after,
            ignore_dirs=ignore.IGNORE_DIRS,
            **options,
        )
        return {"engine": "python", "mode": mode, **found}

    grep.__name__ = "grep"
    grep.__doc__ = SCHEMA["function"]["description"]
    grep.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="grep",
        category="search",
        risk_level="low",
        capabilities=["search"],
        requires_approval=False,
    )
    grep.__tool_schema__ = SCHEMA
    attach_contract(grep, GREP_CONTRACT)
    return [grep]


def _missing_path(path: str) -> str:
    sentence = f"no file or folder at {path!r}"
    if any(mark in path for mark in "|,;"):
        return (
            f"{sentence}: path takes ONE folder or file. To search several, call grep once per "
            "folder, or search their common parent and narrow with glob."
        )
    return f"{sentence}. Check the spelling, or search its parent folder."


def _lines(value: Any, default: int = 0) -> int:
    """A context-line count from the model: an int in `0..MAX_CONTEXT`, else `default`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, min(value, MAX_CONTEXT))


def _py_grep(root: Path, base: Path, pattern: str, glob: Optional[str], n: int) -> dict[str, Any]:
    """Content-mode Python search. It is the entry point from before there were modes, kept for its
    callers."""
    return py_search(
        root=root,
        base=base,
        pattern=pattern,
        glob=glob,
        n=n,
        mode=CONTENT,
        context=0,
        ignore_dirs=ignore.IGNORE_DIRS,
    )
