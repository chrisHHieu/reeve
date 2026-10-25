"""Post-edit evidence appended to a write tool's result.

Two facts the harness can prove about a file that was just written, in the same step the
model reads the result:

- **syntax** — the file no longer parses (stdlib parsers only; `harness/tools/syntax.py`);
- **diagnostics** — the language server reports errors for it, when one resolves
  (`harness/integrations/lsp/manager.py`; nothing is installed or started for this feature).

Both are asides on the edit, never rewrites of the tool's output, and both are
fail-open: the write already succeeded, so no note may cost the call. The engine's
`_record_result` calls these and attaches the results under their own keys (`syntax`,
`diagnostics` for a dict result; appended text otherwise).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from harness.safety.boundary import write_paths

#: How many files of one call are checked. A patch can touch many; the first few are
#: where the change the model just made lives.
MAX_FILES_PER_CALL = 3

#: The diagnostics note is deliberately tighter than the `lsp` tool's own
#: budget (400 ms settle / 8 s deadline): a note must never hold a turn while a server
#: indexes a repo, and the first errors are what the model acts on.
LSP_MAX_FILES = 3
LSP_MAX_ITEMS = 5
LSP_SETTLE_MS = 300
LSP_TIMEOUT_MS = 3_000


def _error_key(item: Mapping[str, Any]) -> str:
    """One diagnostic's identity across edits: what it says and who says it, not where."""
    return f"{item.get('source') or ''}|{item.get('code') or ''}|{item.get('message') or ''}"


def syntax_note(
    status: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    workspace_root: str | Path,
    write_tools: frozenset[str],
) -> Optional[str]:
    """A parse failure in what a write tool just wrote, or None."""
    if status != "ok" or tool_name not in write_tools:
        return None
    from harness.tools.syntax import check_file, supports

    paths, located = write_paths(tool_name, dict(arguments or {}))
    if not located:
        return None
    root = Path(str(workspace_root))
    notes: list[str] = []
    for raw in paths[:MAX_FILES_PER_CALL]:
        if not raw or not supports(raw):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        note = check_file(candidate, display=raw)
        if note:
            notes.append(note)
    if not notes:
        return None
    joined = "\n".join(notes)
    return (
        f"[syntax check] the file now fails to parse — fix it before continuing:\n{joined}"
    )


def lsp_diagnostics_note(
    manager: Any,
    status: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    workspace_root: str | Path,
    write_tools: frozenset[str],
    reported: Optional[dict[str, set[str]]] = None,
) -> Optional[str]:
    """Language-server ERRORS for the files this successful write touched.

    Only when a server RESOLVES — nothing is installed or started for this feature —
    only errors (warnings are noise in the edit loop; the `lsp` tool is how to ask for
    the full picture), only the first few paths, capped and clipped, on a tight budget.
    Fail-open on every axis: no manager, no server, disabled, a timeout, a crashed
    server, a malformed payload.

    `reported` (the session's memory, per file) makes the note say each error ONCE. Session
    b3a8491e-9db (2026-09-28) edited one file eleven times, and every edit carried the same
    `Import "sqlalchemy" could not be resolved` from line 6 — an environment the language
    server could not see, not anything the edit wrote. An error is keyed by its message and
    source, not its line (edits move lines), and the memory is REPLACED each time, so an
    error that was fixed and comes back is reported again. Errors already reported that
    are still there are counted in one line instead of repeated.
    """
    if status != "ok" or tool_name not in write_tools or manager is None:
        return None
    from harness.config.config import load_config

    root = Path(str(workspace_root))
    try:
        table = load_config(root).lsp
    except Exception:  # config trouble must never cost a write its result
        table = {}
    if isinstance(table, dict) and table.get("diagnostics_after_edit", True) is False:
        return None
    paths, located = write_paths(tool_name, dict(arguments or {}))
    if not located:
        return None
    notes: list[str] = []
    for raw in paths[:LSP_MAX_FILES]:
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            result = manager.request(
                "diagnostics",
                candidate,
                workspace=root,
                settle_ms=LSP_SETTLE_MS,
                timeout_ms=LSP_TIMEOUT_MS,
            )
        except Exception:
            continue  # a diagnostic that costs the call is worse than none
        if not isinstance(result, dict) or result.get("error"):
            continue  # no server / disabled / timeout: silence, never a note
        errors = [
            item
            for item in result.get("diagnostics") or []
            if isinstance(item, dict) and item.get("severity") == "error"
        ]
        known: set[str] = set()
        if reported is not None:
            file_key = os.path.normcase(str(candidate))
            known = reported.get(file_key, set())
            reported[file_key] = {_error_key(item) for item in errors}
        fresh = [item for item in errors if _error_key(item) not in known]
        still = len(errors) - len(fresh)
        if not fresh:
            continue
        shown = fresh[:LSP_MAX_ITEMS]
        lines = []
        for item in shown:
            source = str(item.get("source") or "")
            lines.append(
                f"{raw}:{item.get('line', 1)}:{item.get('character', 1)}: "
                f"{item.get('message', '')}".strip()
                + (f" ({source})" if source else "")
            )
        more = len(fresh) - len(shown)
        if more > 0:
            lines.append(f"… and {more} more error(s)")
        if still > 0:
            lines.append(f"({still} error(s) reported for {raw} earlier are still there)")
        notes.append("\n".join(lines))
    if not notes:
        return None
    joined = "\n".join(notes)
    return (
        "[lsp] the language server reports errors in what you just wrote — fix them "
        f"or say why they are pre-existing:\n{joined}"
    )
