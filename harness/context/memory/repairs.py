"""What this project has repaired, kept by the harness for the day it breaks again.

A memory note is a fact the model chose to write. This is the other kind of experience: a
fact the harness witnessed. A check named a failure, files were edited, the same check
stopped naming it (`repair_facts.py`). The harness writes that down in a sidecar it owns
(`.repairs.json` in the project's memory directory, beside `.anchors.json`): which
failures, which check, which files, what each looked like before and after the repair
(digests; "before" from the undo store's before-image of the repair's first edit to it,
when one was kept), what the repair changed in it (a short diff of before against after,
bounded and with credentials masked), how many steps it took, when, in which session and
turn.

When a later check names one of those failures again, the record is what the session is
told: where the last repair was, what it changed, and what became of that place since. A
file back as it was before the repair is said as that, apart from one changed some other
way: it is the commonest way a repaired failure returns (the repair was undone, reverted,
or merged away). Where alone did not save a step: told the file was back as before, a
model still read the whole path again before it acted (the recurrence eval, 2026-10-07:
`to_fix` x1.02 against nothing kept, while a lesson that named the cause read x0.76). The
change is the cause as the harness saw it, with no model's words in it. It is said as what
happened, never as what to do. The same name can fail for a new reason.

No model writes here, so nothing here passes the memory gate: there is no model's claim to
judge. What a model may add is a memory of its own. A repair that took many steps is the
one moment the harness asks for one (`lesson_request`), in the turn and once; the `lesson`
it writes is an ordinary memory file that passes the door like any other. The two are
joined by what the harness already records of each: a lesson saved in the turn a repair was
made in, naming a file that repair edited, is recalled with it (`.anchors.json` holds the
session, the turn and the files a note names).

Fail-open throughout: a sidecar that cannot be read or written means nothing is recalled,
never that a turn stops.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from harness.tools.checks import is_setup, shown_command, spoken
from harness.safety.redaction import redact_secrets
from . import anchors as _anchors
from . import memory_format as _fmt
from .anchors import digest

SIDECAR_NAME = ".repairs.json"
#: The newest repairs kept. A project's old failures matter less than its recent ones.
MAX_REPAIRS = 200
#: The files of one repair that are kept, and the repairs one note names.
MAX_FILES = 12
MAX_TOLD = 2
#: A repair of this many steps or more is one worth a lesson. The upper quarter of what real
#: repairs took: 7 of the 27 in the session store on 2026-10-07 (their median was 5).
COSTLY_STEPS = 8
#: The "before" of a file the repair created: it did not exist.
ABSENT = "absent"
#: What one file's change keeps: its diff lines, and its characters. A repair is usually a
#: few lines; one that rewrote a file is said to be larger and not carried.
MAX_CHANGE_LINES = 24
MAX_CHANGE_CHARS = 1_200
#: A file the change is not read from: larger than this, or not text.
_MAX_CHANGE_SOURCE = 256 * 1024
#: What one note shows of changes, across its repairs.
MAX_SHOWN_CHANGE_CHARS = 2_400
_LESSON_KIND = "lesson"
_DESCRIPTION_CHARS = 160
_SHOWN_MARKS = 3
_SHOWN_FILES = 5


def _named(items: list[str], limit: int) -> str:
    shown = ", ".join(f"`{item}`" for item in items[:limit])
    more = len(items) - limit
    return f"{shown} and {more} more" if more > 0 else shown


def _text_of(raw: Optional[bytes]) -> Optional[str]:
    """A file's text for a diff: "" when it did not exist, None when it is not text."""
    if raw is None:
        return ""
    if len(raw) > _MAX_CHANGE_SOURCE or b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def change_between(before: Optional[bytes], after: Optional[bytes]) -> str:
    """What a repair changed in one file, as hunks of a unified diff (no file header: the
    path is said beside it), one line of context. `None` for a file that did not exist on
    that side. "" when there is nothing to say: no change, not text, or a change too large
    to be one repair's point (more than `MAX_CHANGE_LINES` changed lines). Credentials are
    masked before anything is kept."""
    old, new = _text_of(before), _text_of(after)
    if old is None or new is None:
        return ""
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), n=1, lineterm=""))[2:]
    changed = sum(1 for line in lines if line[:1] in "+-")
    if changed > MAX_CHANGE_LINES:
        return ""
    text = redact_secrets("\n".join(lines))
    return text if len(text) <= MAX_CHANGE_CHARS else ""


class Repairs:
    """One project's repairs. `resolve` turns a path an edit named into the file it is (or
    None), `stamp` gives the session and turn a record is written in, and `asks` says
    whether this session may be asked for a lesson (the person's memory setting)."""

    def __init__(
        self,
        root: Path,
        *,
        resolve: Optional[Callable[[str], Optional[Path]]] = None,
        stamp: Optional[Callable[[], tuple[str, Optional[int]]]] = None,
        asks: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.root = Path(root)
        self._resolve = resolve or (lambda raw: None)
        self._stamp = stamp or (lambda: ("", None))
        self._asks = asks or (lambda: True)

    def asks(self) -> bool:
        return bool(self._asks())

    def resolve(self, raw: str) -> Optional[Path]:
        """The file a path an edit named is, or None."""
        return self._resolve(raw)

    def _lessons(self, row: dict[str, Any], notes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
        """The lessons saved with a repair: `lesson` memories written in the session and
        turn it was made in that name a file it edited (any, when it edited none it could
        name), and are still there. Each as its file and its one-line description."""
        edited = {os.path.normcase(item["at"]) for item in row.get("files") or [] if item.get("at")}
        found = []
        for name, entry in sorted(notes.items()):
            if entry.get("kind") != _LESSON_KIND:
                continue
            if entry.get("session") != row.get("session") or entry.get("turn") != row.get("turn"):
                continue
            named = {os.path.normcase(str(item.get("at"))) for item in entry.get("anchors") or [] if item.get("at")}
            if edited and not (edited & named):
                continue
            try:
                meta, _ = _fmt.split((self.root / name).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue  # the person or a later session deleted it
            said = " ".join(str(meta.get("description") or "").split())[:_DESCRIPTION_CHARS]
            found.append({"name": name, "description": said})
        return found

    def has_lesson(self, marks: Iterable[str]) -> bool:
        """Whether a repair of any of these failures already has a lesson saved with it."""
        wanted = set(marks)
        notes = _anchors.recorded(self.root)
        return any(
            wanted & set(row.get("marks") or []) and self._lessons(row, notes) for row in self._load()
        )

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads((self.root / SIDECAR_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data.get("repairs") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict) and row.get("marks")]

    def _save(self, rows: list[dict[str, Any]]) -> None:
        path = self.root / SIDECAR_NAME
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "repairs": rows[-MAX_REPAIRS:]}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)

    def record(
        self,
        marks: Iterable[str],
        command: str,
        files: Iterable[str],
        steps: int,
        before: Optional[Mapping[str, str]] = None,
        changes: Optional[Mapping[str, str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Write one repair down. `before` maps an edited path to its digest before the
        repair (`ABSENT` when the repair created it), and `changes` to what the repair
        changed in it (`change_between`); a path missing from either has no known before or
        no change to show. A second repair of the same failures in the same turn replaces the
        first: it is the same repair, seen again."""
        names = sorted(set(marks))
        if not names:
            return None
        before = before or {}
        changes = changes or {}
        session, turn = self._stamp()
        entry = {
            "id": hashlib.sha1(f"{names}|{session}|{turn}".encode("utf-8")).hexdigest()[:12],
            "marks": names,
            "command": shown_command(redact_secrets(str(command))),
            "files": [
                {
                    "path": raw,
                    "at": str(at) if at else "",
                    "digest": digest(at) if at else "",
                    **({"before": before[raw]} if at and before.get(raw) else {}),
                    **({"change": changes[raw]} if at and changes.get(raw) else {}),
                }
                for raw, at in ((raw, self._resolve(raw)) for raw in list(files)[:MAX_FILES])
            ],
            "steps": int(steps),
            "at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
            "session": session,
            "turn": turn,
        }
        rows = [row for row in self._load() if row.get("id") != entry["id"]]
        self._save([*rows, entry])
        return entry

    def recall(self, marks: Iterable[str]) -> list[dict[str, Any]]:
        """The repairs that cleared a failure in `marks`, newest first, at most `MAX_TOLD`.
        Not one made in this very turn: the session is still looking at that one. Each
        comes with the failures it shares (`shared`), what became of its files since (`undone`:
        back as they were before the repair; `changed`: neither as the repair left them nor
        as before; `gone`), and any lesson saved with it (`lessons`)."""
        wanted = set(marks)
        session, turn = self._stamp()
        notes = _anchors.recorded(self.root)
        found = []
        for row in reversed(self._load()):
            shared = sorted(wanted & set(row.get("marks") or []))
            if not shared or (row.get("session") == session and row.get("turn") == turn and session):
                continue
            undone, changed, gone = [], [], []
            for item in row.get("files") or []:
                at = Path(item["at"]) if item.get("at") else None
                if at is None or not (item.get("digest") or item.get("before")):
                    continue
                now = digest(at) if at.exists() else ABSENT
                after = item.get("digest") or ""
                if now == after or (now == ABSENT and not after):
                    continue  # as the repair left it, or as nothing says otherwise
                if now == item.get("before"):
                    undone.append(item["path"])
                elif now == ABSENT:
                    gone.append(item["path"])
                else:
                    changed.append(item["path"])
            found.append(
                {
                    **row, "shared": shared, "undone": undone, "changed": changed, "gone": gone,
                    "lessons": self._lessons(row, notes),
                }
            )
            if len(found) >= MAX_TOLD:
                break
        return found

    def told(self, found: list[dict[str, Any]]) -> None:
        """Count that these repairs were recalled to a session."""
        ids = {row.get("id") for row in found}
        rows = self._load()
        for row in rows:
            if row.get("id") in ids:
                row["recalled"] = int(row.get("recalled") or 0) + 1
        self._save(rows)


def note(found: list[dict[str, Any]]) -> str:
    """What a session is told about the repairs its failing check matched: what happened
    then, what that repair changed, and what has changed since. A fact about the past, not
    an instruction. A change is shown for a file that is no longer as the repair left it
    (one that is cannot be why the failure is back), within `MAX_SHOWN_CHANGE_CHARS`."""
    lines = ["System note: this project has repaired this failure before."]
    blocks: list[str] = []
    room = MAX_SHOWN_CHANGE_CHARS
    for row in found:
        day = str(row.get("at") or "")[:10]
        files = [str(item.get("path")) for item in row.get("files") or [] if item.get("path")]
        marks = [spoken(mark) for mark in row["shared"]]
        if all(is_setup(mark) for mark in row["shared"]):
            # What kept the check from running: the fix is usually how it was run.
            line = f"The check could not run ({_named(marks, _SHOWN_MARKS)}) until"
            line += f" {day}" if day else " an earlier session"
            line += f", when it ran as `{redact_secrets(str(row.get('command') or ''))}`"
            line += f" after edits to {_named(files, _SHOWN_FILES)}" if files else ""
        else:
            line = f"{_named(marks, _SHOWN_MARKS)} stopped failing"
            line += f" on {day}" if day else ""
            line += f" after edits to {_named(files, _SHOWN_FILES)}" if files else " after an edit this record cannot name"
        line += f" ({row.get('steps')} steps then)." if row.get("steps") else "."
        if row.get("undone"):
            line += f" Back as before that repair: {_named(row['undone'], _SHOWN_FILES)}."
        if row.get("changed"):
            line += f" Changed since: {_named(row['changed'], _SHOWN_FILES)}."
        if row.get("gone"):
            line += f" No longer there: {_named(row['gone'], _SHOWN_FILES)}."
        for lesson in row.get("lessons") or []:
            line += f" A note was saved with that repair: `{lesson['name']}`"
            line += f" ({lesson['description']})." if lesson["description"] else "."
        lines.append(line)
        moved = {*(row.get("undone") or []), *(row.get("changed") or [])}
        for item in row.get("files") or []:
            change = str(item.get("change") or "")
            if not change or item.get("path") not in moved or len(change) > room:
                continue
            room -= len(change)
            blocks.append(f"What that repair changed in `{item['path']}`:\n```diff\n{change}\n```")
    caveat = (
        "The same name can fail for a new reason: this says where the last repair was, "
        "not what is wrong now."
    )
    said = " ".join(lines)
    return "\n\n".join([said, *blocks, caveat]) if blocks else f"{said} {caveat}"


def lesson_request(marks: Iterable[str], steps: int) -> str:
    """What a session is told when a repair cost enough to be worth remembering why. It
    asks for the cause, leaves "nothing" open, and names no file: where a memory goes and
    what a `lesson` holds is the memory section's to say."""
    names = [spoken(mark) for mark in sorted(marks)]
    return (
        f"System note: {_named(names, _SHOWN_MARKS)} took {steps} steps to repair. If the cause "
        "would not be plain to someone who meets this failure again, save it now as a `lesson` "
        "memory: what was wrong and what fixed it, with the files in backticks. If it was an "
        "ordinary slip, save nothing and go on."
    )


__all__ = [
    "ABSENT", "COSTLY_STEPS", "MAX_CHANGE_CHARS", "MAX_CHANGE_LINES", "MAX_SHOWN_CHANGE_CHARS", "change_between", "MAX_FILES", "MAX_REPAIRS", "MAX_TOLD", "SIDECAR_NAME", "Repairs", "lesson_request", "note",
]
