"""What a person sees of the file tools: the summary line and the card for each call and result.

Presenters run through the `safe_*` wrappers, so one that fails costs a plainer row and never a call.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import cards
from ..contract import ToolPresentation, clip


def _human_bytes(text: Any) -> str:
    size = len(str(text or "").encode("utf-8", "replace"))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _read_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    path = clip(arguments.get("path"), 160) or "(no path)"
    start = arguments.get("start_line")
    limit = arguments.get("max_lines")
    # The window a person sees is the window the model asked for. Without it, four reads
    # of one long file are four identical rows and the transcript stops being a record of
    # what was looked at.
    if isinstance(start, int) and start > 1:
        summary = f"Read {path} from line {start}"
    else:
        summary = f"Read {path}"
    if isinstance(limit, int) and limit > 0 and isinstance(start, int) and start > 1:
        summary += f" ({start} - {start + limit - 1})"
    return ToolPresentation(
        summary=summary,
        kind="read",
        card=cards.generic_call(
            summary, kind="read", locations=[cards.location(str(arguments.get("path") or ""))]
            if arguments.get("path")
            else None,
        ),
    )


def _read_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Read a file", kind="read")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary=clip(error), kind="read")
    path = clip(value.get("path") or arguments.get("path"), 160) or "the file"
    if isinstance(value.get("shown"), str):
        return ToolPresentation(summary=f"Looked at {path}", kind="read")
    start = value.get("start_line")
    end = value.get("end_line")
    total = value.get("total_lines")
    detail = ""
    if isinstance(start, int) and isinstance(end, int) and isinstance(total, int):
        detail = f"lines {start}-{end} of {total}"
    window = _read_card_from(card_meta)
    return ToolPresentation(
        summary=f"Read {path}",
        detail=detail,
        kind="read",
        # No card when the metadata is missing: a read card IS the numbered window, and a
        # card without one would be a header over nothing. The row falls back to the text.
        card=cards.read_result(
            window["path"],
            window["lines"],
            window["total_lines"],
            offset=window["offset"],
        )
        if window
        else None,
    )


def _read_card_from(card_meta: Any) -> Optional[dict[str, Any]]:
    """The narrowed read window from `_card_meta`, or None when it is not usable.

    Defensive for the same reason every card model is: this value reaches the presenter
    through a sidecar, and a malformed one must produce a plainer row rather than an
    exception. A raise here would be caught by `safe_present_result` and cost the whole
    presentation, including the sentence that does not depend on the card.
    """
    if not isinstance(card_meta, dict):
        return None
    window = card_meta.get("read")
    if not isinstance(window, dict):
        return None
    path = window.get("path")
    lines = window.get("lines")
    total = window.get("total_lines")
    offset = window.get("offset")
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(lines, list) or not isinstance(total, int) or not isinstance(offset, int):
        return None
    clean: list[dict[str, Any]] = []
    for entry in lines:
        if not isinstance(entry, dict):
            return None
        number, text = entry.get("number"), entry.get("text")
        if not isinstance(number, int) or not isinstance(text, str):
            return None
        clean.append({"number": number, "text": text})
    return {"path": path, "lines": clean, "total_lines": total, "offset": offset}


def _diffs_from(card_meta: Any) -> list[dict[str, Any]]:
    """The hunks the engine computed for this call, or an empty list.

    The engine injects these from its own change log, which is the only place that knows
    what a file held before the call touched it — aisuite's mutation tools never read the
    file they overwrite, so a presenter cannot derive a removal. Validated here so a
    malformed hunk yields the plain row instead of a broken card.
    """
    if not isinstance(card_meta, dict):
        return []
    raw = card_meta.get("diffs")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for hunk in raw:
        if not isinstance(hunk, dict):
            return []
        path, new_text, old_text = hunk.get("path"), hunk.get("new_text"), hunk.get("old_text")
        if not isinstance(path, str) or not isinstance(new_text, str):
            return []
        if old_text is not None and not isinstance(old_text, str):
            return []
        # The engine's exact counts ride along (changes.py): the sides are context
        # windows, so a surface that counts them reports a one-line edit as +5 −5, and
        # one that finds no count reports +0 −0 (GUI tools review, B1).
        counts = {
            key: hunk[key]
            for key in ("added", "removed", "old_start", "new_start")
            if isinstance(hunk.get(key), int) and not isinstance(hunk.get(key), bool)
        }
        out.append(cards.file_diff(path, old_text, new_text, **counts))
    return out


def _write_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    path = clip(arguments.get("path"), 120) or "(no path)"
    # `overwrite=False` is the only case the arguments settle on their own. Whether the
    # file already exists is a filesystem question, and a presenter may not ask one:
    # purity is what lets the same call render the same card on replay.
    verb = "Create" if arguments.get("overwrite") is False else "Write"
    content = arguments.get("content")
    # The proposed content as a diff, so the approval card shows the change rather than
    # describing it. `old_text` is None because the arguments cannot know what is being
    # replaced — the card claims only the additions it can see, which is the rule for a
    # call-time diff too. The RESULT card gets real hunks from the engine's before-image.
    card = None
    if isinstance(content, str) and content:
        card = cards.diff_call(
            f"{verb} {path}",
            [cards.file_diff(str(arguments.get("path") or path), None, content)],
            locations=[cards.location(str(arguments.get("path")))],
        )
    return ToolPresentation(
        summary=f"{verb} {path}",
        detail=_human_bytes(content),
        kind="write",
        card=card,
    )


def _write_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    # aisuite's write_file returns the path it wrote.
    written = clip(value, 120) if isinstance(value, str) and value.strip() else ""
    fallback = clip(arguments.get("path"), 120)
    hunks = _diffs_from(card_meta)
    return ToolPresentation(
        summary=f"Wrote {written or fallback or 'the file'}",
        kind="write",
        card=cards.diff_result(hunks) if hunks else None,
    )


def _replace_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    path = clip(arguments.get("path"), 120) or "(no path)"
    old = arguments.get("old")
    new = arguments.get("new")
    # A replacement states both sides in its arguments, so its call card is an exact diff
    # with no filesystem question asked — the one mutation where a pending card shows the
    # real removal rather than an all-added approximation.
    card = None
    if isinstance(old, str) and isinstance(new, str):
        card = cards.diff_call(
            f"Edit {path}",
            [cards.file_diff(str(arguments.get("path") or path), old or None, new)],
            locations=[cards.location(str(arguments.get("path")))],
        )
    return ToolPresentation(
        summary=f"Edit {path}",
        detail=f"replacing {len(old or '')} characters" if isinstance(old, str) else "",
        kind="write",
        card=card,
    )


def _replace_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    path = clip(arguments.get("path"), 120) or "the file"
    if isinstance(value, dict) and value.get("error"):
        return ToolPresentation(summary=clip(value.get("error")), kind="write")
    hunks = _diffs_from(card_meta)
    replacements = value.get("replacements") if isinstance(value, dict) else None
    detail = (
        f"{replacements} replacement{'' if replacements == 1 else 's'}"
        if isinstance(replacements, int)
        else ""
    )
    return ToolPresentation(
        summary=f"Edited {path}",
        detail=detail,
        kind="write",
        card=cards.diff_result(hunks) if hunks else None,
    )


def _patch_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    """`apply_patch` / `apply_unified_diff` — one card, because they differ only in syntax.

    Neither gets a diff CALL card. Both carry their own patch text, and turning that into
    `{path, old_text, new_text}` hunks would mean a second patch parser living beside the
    one in `boundary.py` that already reads these formats to find the paths. The patch
    text rides in `content` instead, and the real hunks arrive on the result card from the
    engine's before-image.
    """
    patch = arguments.get("patch") or arguments.get("diff") or ""
    text = patch if isinstance(patch, str) else ""
    files = _patch_files(text)
    title = "Apply a patch" if arguments.get("patch") else "Apply a diff"
    if files:
        title += f" to {len(files)} file{'' if len(files) == 1 else 's'}"
    return ToolPresentation(
        summary=title,
        kind="write",
        card=cards.generic_call(
            title,
            kind="write",
            raw_input=text[:4000] if text else None,
            locations=[cards.location(path) for path in files] or None,
        ),
    )


def _patch_files(text: str) -> list[str]:
    """The paths a patch envelope or unified diff names, in first-seen order."""
    found: list[str] = []
    for line in text.splitlines():
        path = ""
        if line.startswith("*** Add File: ") or line.startswith("*** Update File: "):
            path = line.split(": ", 1)[1].strip()
        elif line.startswith("*** Delete File: "):
            path = line.split(": ", 1)[1].strip()
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            path = line[4:].strip()
            # git writes `+++ b/<path>`; the `b/` prefix is not part of the name.
            if path.startswith("b/"):
                path = path[2:]
        if path and path not in found:
            found.append(path)
    return found


def _patch_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if isinstance(value, dict) and value.get("error"):
        return ToolPresentation(summary=clip(value.get("error")), kind="write")
    counts = value if isinstance(value, dict) else {}
    files = counts.get("file_count")
    hunks = counts.get("hunk_count")
    detail = ""
    if isinstance(files, int):
        detail = f"{files} file{'' if files == 1 else 's'}"
        if isinstance(hunks, int):
            detail += f", {hunks} hunk{'' if hunks == 1 else 's'}"
    diffs = _diffs_from(card_meta)
    return ToolPresentation(
        summary=f"Applied a patch to {detail}" if detail else "Applied a patch",
        detail="",
        kind="write",
        card=cards.diff_result(diffs) if diffs else None,
    )


def _listfiles_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    pattern = clip(arguments.get("pattern"), 120) or "*"
    return ToolPresentation(
        summary=f"List files matching {pattern}",
        kind="search",
        card=cards.generic_call(
            f"List files matching {pattern}", kind="search", raw_input=pattern
        ),
    )


def _listfiles_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if isinstance(value, dict) and value.get("error"):
        return ToolPresentation(summary=clip(value.get("error")), kind="search")
    # `list_files` returns a bare list of paths, and some builds wrap it in `{"files": …}`.
    found = value.get("files") if isinstance(value, dict) else value
    paths = [str(p) for p in found] if isinstance(found, list) else []
    if not paths:
        return ToolPresentation(
            summary=f"No files matching {clip(arguments.get('pattern'), 60) or '*'}",
            kind="search",
        )
    return ToolPresentation(
        summary=f"Found {len(paths)} file{'' if len(paths) == 1 else 's'}",
        kind="search",
        card=cards.search_paths(paths),
    )
