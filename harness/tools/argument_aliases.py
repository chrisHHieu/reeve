"""Parameter names a model brings from other harnesses, taken for Reeve's own.

Models trained on other coding agents send those agents' names: `old_string`/`new_string` for
`replace_in_file`, `offset`/`limit`/`end_line` for `read_file`, `head_limit` for `grep`. Each one
used to be refused with the right names listed, and the retry re-read the whole context. The
calls below are taken as Reeve's, before the call enters the transcript, so the history, the log
and what runs agree; the log keeps what was sent under `renamed`.

A name is taken only when the tool does not accept it itself (`grep` has an `offset` of its own)
and the call does not already carry Reeve's name. The table holds only names measured on the
session store; anything else is still refused with its "did you mean".
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

#: tool -> {name the model sent: Reeve's name}. Measured on the store 2026-10-01..21: 4
#: `replace_in_file` calls with `old_string`/`new_string`, 3 `read_file` with `limit`/`offset`, 1
#: `grep` with `head_limit`; `file_path` is the same tools' path under the other harnesses' name.
RENAMES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path", "offset": "start_line", "limit": "max_lines"},
    "write_file": {"file_path": "path"},
    "replace_in_file": {"file_path": "path", "old_string": "old", "new_string": "new"},
    "grep": {"head_limit": "max_results"},
}


def accepted_names(spec: Any) -> Optional[set[str]]:
    """The keyword names a registered tool takes, or None when they cannot be read (then the
    table is trusted as written)."""
    if spec is None:
        return None
    try:
        params = inspect.signature(spec.func).parameters.values()
    except (TypeError, ValueError, AttributeError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return {p.name for p in params}


def take_aliases(name: str, arguments: Any, accepted: Optional[set[str]] = None) -> dict[str, Any]:
    """Rename the other harnesses' names in `arguments`, in place; returns what was sent, as
    `{name sent: value sent}`, for the log. Empty when nothing was taken."""
    table = RENAMES.get(name)
    if not table or not isinstance(arguments, dict):
        return {}

    def takes(sent: str, ours: str) -> bool:
        if sent not in arguments or ours in arguments:
            return False
        return accepted is None or (sent not in accepted and ours in accepted)

    sent_values: dict[str, Any] = {}
    for sent, ours in table.items():
        if takes(sent, ours):
            sent_values[sent] = arguments[sent]
            arguments[ours] = arguments.pop(sent)
    # `end_line` is the last line wanted; Reeve counts lines from `start_line`.
    if name == "read_file" and takes("end_line", "max_lines"):
        try:
            count = int(arguments["end_line"]) - int(arguments.get("start_line") or 1) + 1
        except (TypeError, ValueError):
            count = 0
        if count >= 1:
            sent_values["end_line"] = arguments.pop("end_line")
            arguments["max_lines"] = count
    return sent_values
