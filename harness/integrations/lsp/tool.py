"""The `lsp` tool — semantic navigation through the project's language server.

`grep` and `read_file` answer "where is this string?"; a language server answers "where
is this SYMBOL defined?", "who references it?", "what type is it?", and "does this file
have errors?". That distinction is the whole point of the tool: a rename-aware reference
search is not something a text search can stand in for.

The tool is READ-ONLY and fail-soft. Every operation either returns a normalized value
dict or `{"error": …}` — a missing server, an unsupported capability, a crashed process
and a timeout are all ordinary outcomes of talking to a program the user may not have
installed, and none of them may take down a turn.

Positions are 1-based on the way in (`line`, `character`), matching every line number
this harness prints; `lsp/client.to_lsp_position` converts to LSP's 0-based coordinates.
The server's character unit is UTF-16 code units, LSP's default negotiation — see the
module docstring in `client.py`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root
from harness.tools import cards
from harness.tools.contract import ToolContract, ToolPresentation, attach_contract, clip
from harness.tools.files.access import escape_refusal as _path_escapes
from .manager import DEFAULT_MAX_RESULTS, MAX_RESULTS_CAP, LSPManager, default_manager

logger = logging.getLogger("harness.integrations.lsp")

_OPERATIONS = (
    "definition",
    "references",
    "hover",
    "document_symbol",
    "workspace_symbol",
    "diagnostics",
)
_POSITION_OPERATIONS = frozenset({"definition", "references", "hover"})

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lsp",
        "description": (
            "Navigate code semantically through the project's language server — "
            "`definition` (where is this symbol defined), `references` (who uses it), "
            "`hover` (its type/signature/doc), `document_symbol` (this file's outline), "
            "`workspace_symbol` (search symbols by name across the project), and "
            "`diagnostics` (errors/warnings for a file). Prefer this over `grep` for "
            "questions about a SYMBOL: it understands imports and types, not just text. "
            "A server must be installed for the file's language (Python: "
            "pyright-langserver, TypeScript/JavaScript: typescript-language-server, Go: "
            "gopls, Rust: rust-analyzer); when one is missing the result says so and how "
            "to fix it. `line`/`character` are 1-based positions into `file` and are "
            "required for definition/references/hover — take the line from a `grep` match "
            "or a `document_symbol`/`workspace_symbol` result (`read_file` shows text "
            "without line numbers, so do not count lines by hand). Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_OPERATIONS),
                    "description": (
                        "What to ask the language server. `diagnostics` waits briefly "
                        "for the server to finish analyzing the file, so the first call "
                        "on a large file may take a few seconds."
                    ),
                },
                "file": {
                    "type": "string",
                    "description": (
                        "The file to ask about, relative to the workspace (or absolute "
                        "inside a session root). Its extension picks the language server "
                        "— even `workspace_symbol` searches the project the file belongs to."
                    ),
                },
                "line": {
                    "type": "integer",
                    "description": (
                        "1-based line into `file`. Required for definition, references "
                        "and hover."
                    ),
                },
                "character": {
                    "type": "integer",
                    "description": (
                        "1-based character within `line` (default 1, the start of the "
                        "line). Used by definition, references and hover."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Name (or fragment) to search for as `workspace_symbol`.",
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        f"Cap on returned rows (default {DEFAULT_MAX_RESULTS}, max "
                        f"{MAX_RESULTS_CAP})."
                    ),
                },
            },
            "required": ["operation", "file"],
        },
    },
}


def lsp_tools(
    workspace: str,
    roots: Optional[list] = None,
    *,
    manager: Optional[LSPManager] = None,
) -> list:
    """Build the `lsp` tool rooted at `workspace`.

    `roots` is kept as the LIVE list and re-resolved per call, exactly like
    `file_tools`: `add_root` mutates it in place when the user grants a folder
    mid-session, and a construction-time copy would make `lsp` refuse a file the very
    same turn `read_file` accepted.

    `manager` is the caller's LSP manager (the `SessionManager` owns one) so every
    engine on one root shares its language servers; None falls back to the process-wide
    manager used by the CLI and direct `build_engine` callers.
    """
    root = Path(workspace).resolve()
    client_manager = manager or default_manager()

    def lsp(
        operation: str,
        file: str = "",
        line: Optional[int] = None,
        character: Optional[int] = None,
        query: str = "",
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> dict[str, Any]:
        operation = str(operation or "").strip().lower()
        if operation not in _OPERATIONS:
            return {
                "error": (
                    f"unknown lsp operation {operation!r}; expected one of: "
                    + ", ".join(_OPERATIONS)
                )
            }
        name = str(file or "").strip()
        if not name:
            return {"error": f"{operation} needs `file` (a path inside the workspace)"}
        # The same resolution and containment rule `read_file` uses: relative to the
        # primary workspace, absolute paths allowed anywhere inside a session root.
        target = (root / name).resolve()
        allowed = allowed_dirs(root, roots)
        if containing_root(target, allowed) is None:
            return {"error": _path_escapes(allowed)}
        if not target.is_file():
            return {"error": f"not a file: {name}"}
        if operation in _POSITION_OPERATIONS:
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                return {
                    "error": (
                        f"{operation} needs `line` (1-based) and optionally "
                        "`character` (1-based)"
                    )
                }
            if not isinstance(character, int) or isinstance(character, bool) or character < 1:
                character = 1
        try:
            return client_manager.request(
                operation,
                target,
                workspace=root,
                roots=allowed,
                line=line,
                character=character,
                query=query,
                max_results=max_results,
            )
        except Exception as exc:
            # Belt and braces: `LSPManager.request` already maps the failures it knows,
            # but this tool's contract is that it NEVER raises — a language server is
            # third-party code and the turn must survive whatever it does.
            logger.warning("lsp tool: %s failed", operation, exc_info=True)
            return {"error": f"lsp {operation} failed: {exc}"}

    lsp.__name__ = "lsp"
    lsp.__doc__ = _SCHEMA["function"]["description"]
    lsp.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="lsp",
        category="lsp",
        risk_level="low",
        capabilities=["read"],
        requires_approval=False,
    )
    lsp.__tool_schema__ = _SCHEMA
    attach_contract(lsp, LSP_CONTRACT)
    return [lsp]


# -- model-facing render ----------------------------------------------------------


def _location_rows(locations: Any) -> list[str]:
    rows: list[str] = []
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        head = (
            f"{location.get('path')}:{location.get('line')}:{location.get('character')}"
        )
        snippet = location.get("snippet")
        rows.append(f"{head} — {snippet}" if snippet else head)
    return rows


def _symbol_rows(symbols: Any, depth: int = 0) -> list[str]:
    rows: list[str] = []
    for symbol in symbols if isinstance(symbols, list) else []:
        if not isinstance(symbol, dict):
            continue
        indent = "  " * depth
        head = f"{indent}{symbol.get('name')} ({symbol.get('kind')})"
        detail = symbol.get("detail")
        if detail:
            head += f" — {detail}"
        rows.append(f"{head} {symbol.get('line')}:{symbol.get('character')}")
        rows.extend(_symbol_rows(symbol.get("children"), depth + 1))
    return rows


def _workspace_symbol_rows(symbols: Any) -> list[str]:
    rows: list[str] = []
    for symbol in symbols if isinstance(symbols, list) else []:
        if not isinstance(symbol, dict):
            continue
        where = (
            f"{symbol.get('path')}:{symbol.get('line')}:{symbol.get('character')}"
            if symbol.get("path")
            else f"line {symbol.get('line')}"
        )
        row = f"{symbol.get('name')} ({symbol.get('kind')}) {where}"
        if symbol.get("container"):
            row += f" — {symbol['container']}"
        rows.append(row)
    return rows


def _diagnostic_rows(diagnostics: Any, file: str = "") -> list[str]:
    rows: list[str] = []
    for diagnostic in diagnostics if isinstance(diagnostics, list) else []:
        if not isinstance(diagnostic, dict):
            continue
        where = f"{diagnostic.get('line')}:{diagnostic.get('character')}"
        if file:
            where = f"{file}:{where}"
        row = f"{diagnostic.get('severity')} {where} {diagnostic.get('message')}"
        if diagnostic.get("source"):
            row += f" [{diagnostic['source']}]"
        if diagnostic.get("code"):
            row += f" ({diagnostic['code']})"
        rows.append(row)
    return rows


def _render(arguments: dict[str, Any], value: Any) -> str:
    """The value as compact text, replacing a JSON object that repeats path/line/character
    keys on every row. The `value` itself (and the card built from it) is unchanged."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    operation = str(value.get("operation") or arguments.get("operation") or "")
    file = str(value.get("file") or arguments.get("file") or "")
    if operation in ("definition", "references"):
        rows = _location_rows(value.get("locations"))
        if not rows:
            return f"No {'definition' if operation == 'definition' else 'references'} found in {file}"
        body = "\n".join(rows)
        if value.get("truncated"):
            body += f"\n(showing {value.get('count')} of {value.get('total')})"
        return f"{file}:\n{body}"
    if operation == "hover":
        contents = str(value.get("contents") or "").strip()
        if not contents:
            return (
                f"No hover information at {file}:{value.get('line')}:"
                f"{value.get('character')}"
            )
        return contents
    if operation == "document_symbol":
        rows = _symbol_rows(value.get("symbols"))
        if not rows:
            return f"No symbols found in {file}"
        body = "\n".join(rows)
        if value.get("truncated"):
            body += f"\n(showing {value.get('count')} of {value.get('total')} symbols)"
        return f"{file}:\n{body}"
    if operation == "workspace_symbol":
        rows = _workspace_symbol_rows(value.get("symbols"))
        query = value.get("query") or arguments.get("query") or ""
        if not rows:
            return f"No workspace symbols match “{query}”"
        body = "\n".join(rows)
        if value.get("truncated"):
            body += f"\n(showing {value.get('count')} of {value.get('total')})"
        return f"Symbols matching “{query}”:\n{body}"
    if operation == "diagnostics":
        rows = _diagnostic_rows(value.get("diagnostics"), file)
        if not rows:
            return f"No diagnostics for {file}"
        body = "\n".join(rows)
        if value.get("truncated"):
            body += f"\n(showing {value.get('count')} of {value.get('total')})"
        return body
    return json.dumps(value, default=str)


# -- what a person sees (tools/contract.py) ----------------------------------------
# Modest on purpose: the answer to a navigation question is a list of places, and the
# sentence + a generic result card carrying those places is the whole presentation. The
# card is built from the same rows the model reads so the two can never disagree.


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    operation = str(arguments.get("operation") or "").strip() or "lsp"
    file = clip(arguments.get("file"), 160) or "(no file)"
    line = arguments.get("line")
    where = f"{file}:{line}" if isinstance(line, int) else file
    summaries = {
        "definition": f"Find the definition in {where}",
        "references": f"Find references in {where}",
        "hover": f"Show the type at {where}",
        "document_symbol": f"List symbols in {file}",
        "workspace_symbol": (
            f"Search project symbols for “{clip(arguments.get('query'), 60)}”"
        ),
        "diagnostics": f"Read diagnostics for {file}",
    }
    summary = summaries.get(operation, f"Run lsp {operation} on {file}")
    kind = "search" if operation in ("references", "workspace_symbol") else "read"
    return ToolPresentation(
        summary=summary,
        kind=kind,
        card=cards.generic_call(
            summary,
            kind=kind,
            locations=[cards.location(str(arguments.get("file")))]
            if arguments.get("file")
            else None,
        ),
    )


def _result_card(content: list[str]) -> Optional[dict[str, Any]]:
    return cards.generic_result(content=content[:12]) if content else None


def _present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Language server finished", kind="read")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary=clip(error), kind="read")
    operation = str(value.get("operation") or arguments.get("operation") or "")
    file = clip(value.get("file") or arguments.get("file"), 120) or "the file"
    if operation in ("definition", "references"):
        total = int(value.get("total") or 0)
        noun = "definition" if operation == "definition" else "reference"
        if not total:
            return ToolPresentation(summary=f"No {noun}s found in {file}", kind="read")
        return ToolPresentation(
            summary=f"Found {total} {noun}{'' if total == 1 else 's'} in {file}",
            detail=f"showing {value.get('count')}" if value.get("truncated") else "",
            kind="search",
            card=_result_card(_location_rows(value.get("locations"))),
        )
    if operation == "hover":
        contents = clip(value.get("contents"), 200)
        where = f"{file}:{value.get('line')}:{value.get('character')}"
        if not contents:
            return ToolPresentation(summary=f"No type information at {where}", kind="read")
        return ToolPresentation(summary=f"Type at {where}", detail=contents, kind="read")
    if operation == "document_symbol":
        count = int(value.get("count") or 0)
        if not count:
            return ToolPresentation(summary=f"No symbols found in {file}", kind="read")
        return ToolPresentation(
            summary=f"Listed {count} symbol{'' if count == 1 else 's'} in {file}",
            kind="read",
            card=_result_card(_symbol_rows(value.get("symbols"))),
        )
    if operation == "workspace_symbol":
        total = int(value.get("total") or 0)
        query = clip(value.get("query") or arguments.get("query"), 60)
        if not total:
            return ToolPresentation(
                summary=f"No project symbols match “{query}”", kind="search"
            )
        return ToolPresentation(
            summary=f"Found {total} project symbols for “{query}”",
            kind="search",
            card=_result_card(_workspace_symbol_rows(value.get("symbols"))),
        )
    if operation == "diagnostics":
        total = int(value.get("total") or 0)
        if not total:
            return ToolPresentation(summary=f"No diagnostics for {file}", kind="read")
        counts = value.get("severity_counts") or {}
        parts = [
            f"{counts[key]} {key}{'' if counts.get(key) == 1 else 's'}"
            for key in ("error", "warning", "info", "hint")
            if counts.get(key)
        ]
        return ToolPresentation(
            summary=f"{total} diagnostic{'' if total == 1 else 's'} in {file}",
            detail=", ".join(parts),
            kind="read",
            card=_result_card(_diagnostic_rows(value.get("diagnostics"), file)),
        )
    return ToolPresentation(summary=f"Language server finished ({operation})", kind="read")


LSP_CONTRACT = ToolContract(
    render=_render,
    present_call=_present_call,
    present_result=_present_result,
    # A cold server may be indexing; the client's own request timeouts (20 s, 60 s on the
    # handshake) fire first, and this budget sits below the runtime's deadline ceiling.
    timeout_ms=120_000,
)
