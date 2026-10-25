"""`present` — declare finished files as deliverables for the person to open.

The model writes a report and says so in prose: "I saved it to `reports/q3.docx`". The
person then has a path inside a sentence and no affordance, and the GUI can only guess
which of the turn's changed files was the deliverable. A present tool answers this
with a tool whose entire job is DECLARING: it exports nothing, changes nothing, and its
whole value is the card it leaves behind (`packages/deliverables/tool-present/src/index.ts`).

Three properties are load-bearing, and each is why something here is shaped the way it is:

- **It only declares.** No write, no mutation, no approval ask: the tool reads `stat`
  (through the containment check) and returns. It declares `READ_ONLY_CAPS`, so the
  permission engine answers "low risk" and the ordinary guard stage runs first — the same
  path every read takes. A declaration that needed consent would be consent people learn
  to click through.
- **The declaration outlives the turn.** The paths and the model's own words ride the
  result as `_card_meta`, which the engine lifts off before the model or the presenter
  sees the value (`engine/authorize.py`, `_SIDECAR_KEYS`) and turns into the `_presentation`
  sidecar on the tool message. That sidecar is what makes the delivery row survive a
  session reload — a card derived on the live event alone would be gone on reopen.
- **The path is what the model wrote, validated.** The path is resolved to decide whether
  it is inside a session root and whether it is a regular file; the DECLARED path is what
  comes back in the card, because it is relative to the session workspace and that is what
  the surface opens (`surfaces/gui/src/components/markdown/Deliverables.tsx`).

Local only, like `file_writers`: the tool resolves this machine's paths, so a remote
session gets no such tool rather than one that validates the controller's disk
while the deliverable lives on the other host.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root
from . import cards
from .contract import READ_ONLY_CAPS, ToolContract, ToolPresentation, attach_contract, clip
from .files.access import escape_refusal as _escapes

#: The enforced ceiling for one call, the `maxFiles` default. Four is the RECOMMENDED
#: count (said in the parameter description, and the GUI folds past four rows); this is
#: the hard stop above it, so a model that declares its whole output directory at once is
#: told to pick rather than given a wall of rows.
_MAX_FILES = 8

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "present",
        "description": (
            "Declare existing files as final deliverables for the user. Use it when the "
            "user needs a separate file, especially Office documents, spreadsheets, and "
            "slide decks; prefer your final response when that suffices. The user opens "
            "the current files; their contents are not copied. Call it BEFORE your final "
            "answer, in the same step as your last working calls when you can, then write the "
            "answer in a message with no tool call: text written beside a tool call is shown "
            "as progress, not as your answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": (
                        "Usually the 1-2 most important deliverables; at most 4 per call."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Path of an existing regular file, relative to the "
                                    "workspace (or absolute inside a session root)."
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": "Brief description for the user.",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            "required": ["files"],
        },
    },
}


def _declared(arguments: Any) -> list[dict[str, Any]]:
    """The entries the model tried to declare, in order, dropping anything unusable.

    A single `files` dict is accepted as one entry: the schema asks for an array, but a
    model that sends the object alone is asking the same thing, and refusing it would cost
    a turn over a shape the tool can read. A blank path is dropped HERE and reported by
    the caller, so the two places that need "which paths did we actually get" (the call
    card and the result summary) cannot disagree.
    """
    if not isinstance(arguments, dict):
        return []
    raw = arguments.get("files")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if not path:
            continue
        description = entry.get("description")
        out.append(
            {
                "path": path,
                "description": str(description) if isinstance(description, str) else "",
            }
        )
    return out


def present_tools(workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None) -> list:
    """`present` rooted at `workspace`. Empty with `backend` (a remote world), whose files
    live on another host — the same rule `file_writers` applies, for the same reason.

    `roots` is held as the LIVE list and re-resolved per call (`allowed_dirs`), so a folder
    the user grants mid-session can hold the deliverable declared in the very next step.
    """
    if backend is not None:
        return []
    root = Path(workspace).resolve()

    def present(files: Any = None) -> dict[str, Any]:
        """Declare files you have finished as deliverables for the user to open.

        Call this for the files the user should open — usually the 1-2 most important, never
        more than 4 in one call — with a short description of each. Keep the
        `[Title](artifact:path)` link in your message too: the link puts the mention in the
        sentence, this puts the file in the row under it. Each path must be an existing
        regular file inside the session's directories (a directory is refused). This tool
        only declares: it writes nothing and changes nothing.
        """
        wanted = _declared({"files": files})
        if not wanted:
            return {
                "error": (
                    "present requires a non-empty file path: pass `files` as one or more "
                    "{path, description} entries, each a file that exists"
                )
            }
        if len(wanted) > _MAX_FILES:
            return {
                "error": (
                    f"present accepts 1 to {_MAX_FILES} files; got {len(wanted)} — declare "
                    "the most important ones (usually 1-2) in this call"
                )
            }
        allowed = allowed_dirs(root, roots)
        declared: list[dict[str, Any]] = []
        for entry in wanted:
            shown = entry["path"]
            target = (root / shown).resolve()
            home = containing_root(target, allowed)
            if home is None:
                # The same sentence every file tool refuses with, so a model that has seen
                # it once recognises it here (`files._escapes` names the remedy).
                return {"error": _escapes(allowed)}
            if not target.is_file():
                if target.is_dir():
                    return {
                        "error": (
                            f"Cannot present {shown}: it is a directory, not a file — "
                            "declare the file inside it"
                        )
                    }
                # The sentence names the path, the
                # lookup that failed, and both remedies, because the caller is a model
                # deciding what to do next rather than a person reading a crash.
                return {
                    "error": (
                        f"Cannot present {shown}: file not found. Check the path, create "
                        "the file if needed, and retry."
                    )
                }
            try:
                label = target.relative_to(home).as_posix() if home == root else str(target)
            except ValueError:  # pragma: no cover - resolved targets are always under home
                label = str(target)
            declared.append({"path": label, "description": entry["description"] or None})
        # The model's own words about each file, carried beside the result the way
        # `read_file` carries its numbered window: the engine lifts `_card_meta` off before
        # either the model or the presenter sees the value (`engine/authorize.py`,
        # `_SIDECAR_KEYS`), and the card is rebuilt from it on the live path AND on replay.
        #
        # No `turn` here: this engine keeps the turn number on the engine, not
        # on a context a tool can read (`tools/output.py` publishes the CALL id, not the
        # turn), and a card that guessed one would be worse than a card without it.
        return {
            "files": declared,
            "_card_meta": {"files": {"files": declared}},
        }

    present.__name__ = "present"
    present.__doc__ = _SCHEMA["function"]["description"]
    present.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="present",
        category="filesystem",
        risk_level="low",
        capabilities=["present"],
        requires_approval=False,
    )
    present.__tool_schema__ = _SCHEMA
    attach_contract(present, PRESENT_CONTRACT)
    return [present]


def _present_render(arguments: dict[str, Any], value: Any) -> str:
    """One line per declared file, replacing the JSON object.

    Same reason as the other renders here (`files.py`): the default encoding repeats
    `path`/`description` per entry and escapes every string, and this result stays in
    context for the rest of the session. The render also tells the model what the person
    now SEES, which is the whole point of declaring rather than mentioning.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        return json.dumps(value, default=str)
    lines = []
    for entry in entries:
        path = str(entry.get("path") or "")
        description = entry.get("description")
        suffix = f" — {description}" if isinstance(description, str) and description else ""
        lines.append(f"Presented {path}{suffix}")
    return "\n".join(lines)


def _files_card_from(card_meta: Any) -> Optional[dict[str, Any]]:
    """The declared files from `_card_meta`, or None when it is not usable.

    Defensive for the same reason every card model here is: the value arrives through a
    sidecar, and a malformed one must render a plainer row rather than raise. A raise
    inside the presenter is caught by `safe_present_result` and costs the whole
    presentation, including the sentence that never depended on the card.
    """
    if not isinstance(card_meta, dict):
        return None
    payload = card_meta.get("files")
    if not isinstance(payload, dict):
        return None
    entries = payload.get("files")
    if not isinstance(entries, list):
        return None
    clean: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        description = entry.get("description")
        if not isinstance(path, str) or not path.strip():
            return None
        if description is not None and not isinstance(description, str):
            return None
        clean.append({"path": path, "description": description})
    return cards.files_result(clean, title=payload.get("title"))


def _present_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    """What the row says BEFORE the call runs: the paths the model just named.

    `kind="write"` on both halves even though the call reads: this presentation category is
    about how the row LOOKS (`presentation.py`), and a handover is the completion of work —
    a read glyph on "Present q3.docx" would read as inspection.
    """
    entries = _declared(arguments)
    if not entries:
        return ToolPresentation(summary="Present the delivered files", kind="write")
    if len(entries) == 1:
        summary = f"Present {_leaf(entries[0]['path'])}"
    else:
        summary = f"Present {len(entries)} files"
    return ToolPresentation(
        summary=summary,
        kind="write",
        card=cards.generic_call(
            summary,
            kind="write",
            locations=[{"path": entry["path"]} for entry in entries],
        ),
    )


def _present_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    """What the row says AFTER the call ran. Pure: no stat, no resolve, no I/O.

    The card comes from `_card_meta` (the DECLARED entries, path already validated), never
    from re-deriving anything off the filesystem — the same call must render the same row
    on replay, when the file may be long gone.
    """
    if isinstance(value, dict):
        error = str(value.get("error") or "").strip()
        if error:
            return ToolPresentation(summary=clip(error), kind="write")
    card = _files_card_from(card_meta)
    if card is None:
        return ToolPresentation(summary="Presented the files", kind="write")
    count = len(card["files"])
    if count == 1:
        summary = f"Presented {_leaf(card['files'][0]['path'])}"
    else:
        summary = f"Presented {count} files"
    return ToolPresentation(
        summary=summary,
        detail=f"{count} file{'' if count == 1 else 's'} for the user to open",
        kind="write",
        card=card,
    )


def _leaf(path: str) -> str:
    """The file's name — a sentence names the file, not its whole path."""
    return path.replace("\\", "/").rsplit("/", 1)[-1] or path


#: `present` is a closure built per workspace, so its contract is attached where the tool
#: is. It declares READ_ONLY_CAPS because it IS one: the call stats a path and returns the
#: declaration. That is what keeps it out of every approval path (`risk.classify` answers
#: READ for a tool with no base entry and `requires_approval=False`), so the ordinary guard
#: stage decides — and there is nothing here for a floor to hold.
PRESENT_CONTRACT = ToolContract(
    render=_present_render,
    present_call=_present_present_call,
    present_result=_present_present_result,
    timeout_ms=10_000,
    capabilities=READ_ONLY_CAPS,
)
