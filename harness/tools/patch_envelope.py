"""The Codex `apply_patch` envelope, parsed and applied to TEXT — no disk, no world.

One core for every world that offers `apply_patch` (the local writer in `file_writers`,
the remote world in `remote_env/files.py`), so the envelope means the same thing
everywhere:

- **One matcher.** A hunk's context/removal lines are found by `edit_matchers
  .find_line_block`, the ladder `replace_in_file` climbs (exact, then ignoring the
  reader's line numbers, then ignoring trailing whitespace the file carries), and the rung
  that matched is reported. A miss names the hunk and the closest text through the same
  `closest_region` a `replace_in_file` miss does.
- **The envelope's own semantics** (Codex `apply-patch`): hunks apply in order, each
  searched from where the previous one ended; `@@ <line>` narrows the search to after that
  line; a hunk with no context or removals inserts after its `@@` line, else at the end;
  `*** End of File` pins a hunk to the file's end. A blank line inside a hunk is a blank
  context line (models drop the leading space).
- **Nothing is written until everything applies.** `plan_patch` returns the new text of
  every file or raises: a patch that fails on its third file leaves the first two
  untouched, so a retry starts from the state the model read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .edit_matchers import closest_region, describe, find_line_block, strip_line_numbers

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_DELETE = "*** Delete File: "
_UPDATE = "*** Update File: "
_MOVE = "*** Move to: "
_EOF = "*** End of File"


@dataclass
class Hunk:
    anchor: str
    old: list[str]
    new: list[str]
    at_end: bool = False


@dataclass
class FileOp:
    kind: str  # "add" | "delete" | "update"
    path: str
    move_to: Optional[str] = None
    content: str = ""
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class PlannedFile:
    """One file's outcome: its new text (`None` for a deletion) and how its hunks matched."""

    op: FileOp
    text: Optional[str]
    notes: list[str] = field(default_factory=list)


def parse_patch(patch: str) -> list[FileOp]:
    lines = str(patch).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0].strip() != _BEGIN:
        raise ValueError("Patch must start with *** Begin Patch.")
    if lines[-1].strip() != _END:
        raise ValueError("Patch must end with *** End Patch.")
    ops: list[FileOp] = []
    index = 1
    body = lines[:-1]
    while index < len(body):
        line = body[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith(_ADD):
            op = FileOp("add", line[len(_ADD):].strip())
            index += 1
            content: list[str] = []
            while index < len(body) and not body[index].startswith("*** "):
                current = body[index]
                if current and not current.startswith("+"):
                    raise ValueError(f"Invalid add-file patch line: {current}")
                content.append(current[1:])
                index += 1
            op.content = "\n".join(content) + "\n" if content else ""
            ops.append(op)
            continue
        if line.startswith(_DELETE):
            ops.append(FileOp("delete", line[len(_DELETE):].strip()))
            index += 1
            continue
        if line.startswith(_UPDATE):
            op = FileOp("update", line[len(_UPDATE):].strip())
            index += 1
            if index < len(body) and body[index].startswith(_MOVE):
                op.move_to = body[index][len(_MOVE):].strip()
                index += 1
            hunk: Optional[Hunk] = None
            while index < len(body) and (
                not body[index].startswith("*** ") or body[index].strip() == _EOF
            ):
                current = body[index]
                index += 1
                if current.strip() == _EOF:
                    if hunk is not None:
                        hunk.at_end = True
                    continue
                if current.startswith("@@"):
                    if hunk is not None and (hunk.old or hunk.new):
                        op.hunks.append(hunk)
                    hunk = Hunk(anchor=current[2:].strip(), old=[], new=[])
                    continue
                if hunk is None:
                    hunk = Hunk(anchor="", old=[], new=[])
                marker, text = (current[:1], current[1:]) if current else (" ", "")
                if marker == " ":
                    hunk.old.append(text)
                    hunk.new.append(text)
                elif marker == "-":
                    hunk.old.append(text)
                elif marker == "+":
                    hunk.new.append(text)
                else:
                    raise ValueError(f"Invalid update patch line: {current}")
            if hunk is not None and (hunk.old or hunk.new):
                op.hunks.append(hunk)
            if not op.hunks and not op.move_to:
                raise ValueError(f"Patch update has no hunks for {op.path}.")
            ops.append(op)
            continue
        raise ValueError(f"Unknown patch operation: {line}")
    if not ops:
        raise ValueError("Patch contains no file operations.")
    return ops


def _find_anchor(lines: list[str], anchor: str, start: int) -> Optional[int]:
    want = anchor.strip()
    for index in range(start, len(lines)):
        if lines[index].strip() == want:
            return index
    return None


def apply_hunks(text: str, hunks: list[Hunk], path: str) -> tuple[str, list[str]]:
    """`text` (LF) with every hunk applied, and a note per hunk that matched by a fallback
    rung. Raises ValueError naming the hunk that missed and where the file comes closest."""
    ends_with_newline = text.endswith("\n")
    lines = text.split("\n")
    if ends_with_newline:
        lines.pop()
    cursor = 0
    notes: list[str] = []
    for number, hunk in enumerate(hunks, 1):
        start = cursor
        if hunk.anchor:
            found = _find_anchor(lines, hunk.anchor, cursor)
            if found is not None:
                start = found + 1
        if not hunk.old:
            at = start if hunk.anchor and start > cursor else len(lines)
            lines[at:at] = hunk.new
            cursor = at + len(hunk.new)
            continue
        match = find_line_block(lines, hunk.old, start, at_end=hunk.at_end)
        if match is None and start > cursor:
            match = find_line_block(lines, hunk.old, cursor, at_end=hunk.at_end)
        if match is None:
            near = closest_region("\n".join(lines), "\n".join(hunk.old))
            raise ValueError(
                f"Patch context does not match {path} — hunk {number} of {len(hunks)} "
                + (f"does not match: {near}" if near else "matches nothing in the file")
                + ". Rebuild that hunk from a fresh read of those lines."
            )
        at, via = match
        new = [strip_line_numbers(line) for line in hunk.new] if via == "line-numbers" else hunk.new
        lines[at : at + len(hunk.old)] = new
        cursor = at + len(new)
        note = describe(via)
        if note:
            notes.append(f"hunk {number}: {note}")
    out = "\n".join(lines)
    return (out + "\n" if ends_with_newline or (lines and not text) else out), notes


def plan_patch(
    patch: str,
    read: Callable[[str], str],
    exists: Callable[[str], bool],
) -> list[PlannedFile]:
    """Every file's new text, or ValueError/FileExistsError/FileNotFoundError before
    anything is written. `read(path)` returns a file's text with CRLF folded to LF;
    `exists(path)` says whether anything is at that path."""
    planned: list[PlannedFile] = []
    # A file an earlier section of this envelope already changed is read as changed: two
    # `*** Update File` sections for one path apply one after the other, as they would
    # have if each had been written before the next was read.
    pending: dict[str, str] = {}
    for op in parse_patch(patch):
        if op.kind == "add":
            if exists(op.path) or op.path in pending:
                raise FileExistsError(
                    f"File already exists: {op.path} — update it with *** Update File, "
                    "or replace it whole with write_file"
                )
            planned.append(PlannedFile(op, op.content))
            pending[op.path] = op.content
        elif op.kind == "delete":
            if op.path not in pending:
                read(op.path)  # the file must exist and be readable
            planned.append(PlannedFile(op, None))
            pending.pop(op.path, None)
        else:
            if op.move_to and exists(op.move_to):
                raise FileExistsError(f"File already exists: {op.move_to}")
            source = pending[op.path] if op.path in pending else read(op.path)
            text, notes = apply_hunks(source, op.hunks, op.path)
            planned.append(PlannedFile(op, text, notes))
            if op.move_to:
                pending.pop(op.path, None)
            pending[op.move_to or op.path] = text
    return planned


def patch_result(planned: list[PlannedFile], label: Callable[[str], str]) -> dict:
    """The tool's result, in the shape the toolkit's `apply_patch` returned (cards and
    presenters read these keys), plus `matched_via` when a hunk matched by a fallback."""
    changed: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    notes: list[str] = []
    hunks = 0
    for item in planned:
        op = item.op
        if op.kind == "delete":
            deleted.append(label(op.path))
            hunks += 1
            continue
        target = label(op.move_to or op.path)
        changed.append(target)
        if op.kind == "add":
            added.append(target)
            hunks += 1
        else:
            hunks += len(op.hunks)
            if op.move_to:
                deleted.append(label(op.path))
            notes.extend(f"{label(op.path)} {note}" for note in item.notes)
    result: dict = {
        "changed_files": changed,
        "added_files": added,
        "deleted_files": deleted,
        "file_count": len(planned),
        "hunk_count": hunks,
    }
    if notes:
        result["matched_via"] = "; ".join(notes)
    return result
