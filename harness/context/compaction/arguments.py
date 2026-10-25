"""Aged write payloads in the outbound view.

A prune pass shortens aged tool RESULTS. The calls that wrote files carry their payload in
the call's ARGUMENTS: the whole new file of a `write_file`, both sides of a
`replace_in_file`, the hunks of an `apply_patch`. Those were sent again on every request for
the rest of the session. Measured 2026-10-06 on the 24 conversations long enough to have an
aged span: the write payloads there were 2.07M characters a stub could free, against 3.43M
the result prune frees.

A write's payload has a recovery path the model already knows how to use: the file is on
disk, and the version there is the one to work from anyway. So past the prune point a
payload over `ARGUMENT_FOLD_CHARS` becomes one line saying how large it was and where it
went. The read guard is told (`engine/view_cut.py`): a file whose only sight in the view was
a folded write is out of view, so writing it whole again needs a fresh read.

Same rules as the result prune: an outbound copy only, never the canonical thread; the list
keeps its length, so every index into it holds; forward-only, because the prune point only
moves forward.
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: The arguments that carry what a write tool wrote. Only these fold: the path, the count of
#: replacements and every other argument stay, so the call still says what it did.
PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "write_file": ("content",),
    "replace_in_file": ("old", "new"),
    "apply_patch": ("patch",),
    "edit_notebook": ("source",),
}
#: A payload this long or shorter stays: the stub would save next to nothing.
ARGUMENT_FOLD_CHARS = 1_000
_PATCH_HEADER = "*** "


def _lines(text: str) -> int:
    return text.count("\n") + 1


def _stub(text: str, path: str) -> str:
    where = f" of {path}" if path else ""
    return (
        f"[{_lines(text):,} lines, {len(text):,} characters{where}: folded to free context. "
        "The file on disk is the version to work from; read_file it before writing it whole.]"
    )


def _fold_patch(patch: str) -> str:
    """The envelope's own header lines (which file, added, deleted, moved) with each file's
    hunks replaced by one line: the patch still names everything it touched."""
    out: list[str] = []
    body: list[str] = []

    def flush() -> None:
        if body:
            text = "\n".join(body)
            out.append(f"[{len(body):,} lines, {len(text):,} characters: folded to free context]")
            body.clear()

    for line in patch.splitlines():
        if line.startswith(_PATCH_HEADER):
            flush()
            out.append(line)
        else:
            body.append(line)
    flush()
    return "\n".join(out)


def fold_arguments(name: str, args: dict[str, Any]) -> Optional[dict[str, Any]]:
    """`args` with its large write payloads folded, or None when nothing would change."""
    fields = PAYLOAD_FIELDS.get(name)
    if not fields or not isinstance(args, dict):
        return None
    folded = dict(args)
    changed = False
    for field in fields:
        value = args.get(field)
        if not isinstance(value, str) or len(value) <= ARGUMENT_FOLD_CHARS:
            continue
        if name == "apply_patch":
            folded[field] = _fold_patch(value)
        else:
            folded[field] = _stub(value, str(args.get("path") or ""))
        changed = True
    return folded if changed else None


def _decoded(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _folded_call(call: dict[str, Any]) -> Optional[dict[str, Any]]:
    function = call.get("function") or {}
    raw = function.get("arguments")
    args = _decoded(raw)
    if args is None:
        return None
    folded = fold_arguments(str(function.get("name") or ""), args)
    if folded is None:
        return None
    # The call keeps the type it had: a provider adapter that sent a JSON string still does.
    encoded: Any = json.dumps(folded, ensure_ascii=False) if isinstance(raw, str) else folded
    return {**call, "function": {**function, "arguments": encoded}}


def fold_write_arguments(messages: list[dict[str, Any]], upto: int) -> list[dict[str, Any]]:
    """An outbound copy with the write payloads of calls before `upto` folded."""
    if upto <= 0:
        return messages
    out = list(messages)
    for index in range(min(upto, len(out))):
        message = out[index]
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            continue
        replaced = [_folded_call(call) if isinstance(call, dict) else None for call in calls]
        if any(item is not None for item in replaced):
            out[index] = {
                **message,
                "tool_calls": [new if new is not None else old for new, old in zip(replaced, calls)],
            }
    return out


def count_folded_arguments(messages: list[dict[str, Any]], start: int, upto: int) -> int:
    """How many calls in `messages[start:upto]` a fold changes: the number a pass folded."""
    count = 0
    for index in range(max(0, start), min(upto, len(messages))):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and _folded_call(call) is not None:
                count += 1
    return count


__all__ = [
    "ARGUMENT_FOLD_CHARS",
    "PAYLOAD_FIELDS",
    "count_folded_arguments",
    "fold_arguments",
    "fold_write_arguments",
]
