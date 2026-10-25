"""What the harness knows about each memory, and what it tells a later session.

The model writes the fact. The harness keeps what a model should not be asked to keep, in a
sidecar it owns (`.anchors.json` in the memory directory): when the note was first written and
last rewritten, in which session and turn, the files it names and what each looked like (a
digest, never content), whether it was written in a turn that read web or connector content,
and how often it has been read since. At the start of a later session the index is loaded with
that record applied: a note whose file changed or is gone says so, ongoing work carries its
date, a note written after outside content says to check the source, and a note nobody has read
for a month says it can go. Each mark states a fact the harness has; none is a guess from age
alone that a note is wrong.

The sidecar and not the memory file, for two reasons. The model does not know a digest and
should not be asked to write one; and rewriting a file the model has just written would make
the read guard call it changed on disk (`tools/observed.py`).

Fail-open throughout: a sidecar that cannot be read means nothing is flagged, never that a
session cannot start.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .gate import INDEX_NAME, LEGACY_INDEX_NAME, RESERVED

SIDECAR_NAME = ".anchors.json"
#: Past this a file is not hashed (a database, an artifact): its memory is never flagged.
MAX_DIGEST_BYTES = 8 * 1024 * 1024
#: Written once per directory when its pre-evidence index is set aside (`retire_legacy_index`).
EVIDENCE_MARKER = ".evidence"
EVIDENCE_VERSION = "evidence-1"
#: A note not read for this long, and at least this old, is marked as one that can go
#: (Anthropic's guidance for a memory directory: expire what has not been accessed).
UNREAD_DAYS = 30

_INDEX_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+)\)")


def digest(path: Path) -> str:
    """The file's content hash; "" for a directory, a missing file or one too large."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_DIGEST_BYTES:
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _load(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / SIDECAR_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(root: Path, data: dict[str, Any]) -> None:
    path = root / SIDECAR_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def record(
    root: Path,
    name: str,
    *,
    kind: str,
    anchors: list[tuple[str, Path]],
    named: list[tuple[str, Path]] = (),
    session: str = "",
    turn: Optional[int] = None,
    external: bool = False,
) -> None:
    """Note what `name` was admitted on: each file it declares (`anchors`) or names in its
    text (`named`), where it resolved and what it looked like; which session and turn saved
    it; whether that turn read outside content. A rewrite keeps when the note was first
    written and how often it has been read."""
    data = _load(root)
    earlier = data.get(name) if isinstance(data.get(name), dict) else {}
    now = _now().isoformat()
    data[name] = {
        "kind": kind,
        "anchors": [
            *({"path": raw, "at": str(at), "digest": digest(at)} for raw, at in anchors),
            *({"path": raw, "at": str(at), "digest": digest(at), "named": True} for raw, at in named),
        ],
        **({"session": session} if session else {}),
        **({"turn": turn} if turn is not None else {}),
        **({"external": True} if external else {}),
        "created": earlier.get("created") or earlier.get("written") or now,
        "written": now,
        **({key: earlier[key] for key in ("reads", "read") if key in earlier}),
    }
    _save(root, data)


def note_read(root: Path, name: str) -> None:
    """Count a read of `name`. Only a note the harness has a record of is counted."""
    data = _load(root)
    entry = data.get(name)
    if not isinstance(entry, dict):
        return
    entry["reads"] = int(entry.get("reads") or 0) + 1
    entry["read"] = _now().isoformat()
    _save(root, data)


def recorded(root: Path) -> dict[str, dict[str, Any]]:
    """Every note's record, by file name; {} when the sidecar cannot be read."""
    return {name: entry for name, entry in _load(root).items() if isinstance(entry, dict)}


def entry_of(root: Path, name: str) -> dict[str, Any]:
    """The record of `name`, or {}."""
    entry = _load(root).get(name)
    return entry if isinstance(entry, dict) else {}


def forget(root: Path, name: str) -> None:
    data = _load(root)
    if data.pop(name, None) is not None:
        _save(root, data)


def stale(root: Path) -> dict[str, list[tuple[str, str]]]:
    """`{memory file: [(anchor as written, "changed" | "gone")]}` for every memory whose
    evidence no longer matches the disk. A directory anchor is only ever "gone"."""
    out: dict[str, list[tuple[str, str]]] = {}
    for name, entry in _load(root).items():
        if not isinstance(entry, dict) or not (root / name).is_file():
            continue
        for anchor in entry.get("anchors") or []:
            if not isinstance(anchor, dict):
                continue
            at, was = Path(str(anchor.get("at") or "")), str(anchor.get("digest") or "")
            if not at.exists():
                out.setdefault(name, []).append((str(anchor.get("path") or at), "gone"))
            elif was and digest(at) != was:
                out.setdefault(name, []).append((str(anchor.get("path") or at), "changed"))
    return out


def _when(stamp: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def annotate(index_text: str, root: Path, now: Optional[datetime] = None) -> str:
    """The index as the model is shown it, with what the harness knows of each note: ongoing
    work carries the date it was last written; a note whose file changed or is gone, one
    written after outside content, and one unread for a month say so. The index on disk is
    not touched, and a note with no record gets no mark."""
    records = _load(root)
    if not records:
        return index_text
    flagged = stale(root)
    now = now or _now()
    lines = []
    for line in index_text.splitlines():
        linked = _INDEX_LINK_RE.search(line)
        entry = records.get(linked.group(1)) if linked else None
        if not isinstance(entry, dict):
            lines.append(line)
            continue
        written = _when(entry.get("written"))
        if entry.get("kind") == "project" and written is not None:
            line = f"{line} (as of {written.date().isoformat()})"
        marks = flagged.get(linked.group(1))
        if marks:
            said = "; ".join(
                f"{path} {'no longer exists' if state == 'gone' else 'changed since this was written'}"
                for path, state in marks[:3]
            )
            line = f"{line}  [check before relying on it: {said}]"
        if entry.get("external"):
            line = f"{line}  [written after reading web or connector content: check it against the source]"
        last = _when(entry.get("read")) or written
        limit = timedelta(days=UNREAD_DAYS)
        if written is not None and last is not None and now - written >= limit and now - last >= limit:
            line = f"{line}  [not read for over {UNREAD_DAYS} days: drop it if it no longer helps]"
        lines.append(line)
    return "\n".join(lines)


def mark_current(root: Path) -> None:
    """A directory that holds no notes yet has nothing from before: say so now, or the
    first notes it is given would be taken for old ones at the next session's start."""
    root = Path(root)
    marker = root / EVIDENCE_MARKER
    if root.is_dir() and not marker.exists() and not any(p.name not in RESERVED for p in root.glob("*.md")):
        marker.write_text(EVIDENCE_VERSION + "\n", encoding="utf-8")


def retire_legacy_index(root: Path) -> int:
    """Set aside the index a directory had before memories carried evidence, once.

    Those notes were saved with nothing to check them against, most by a background pass and
    not by the agent doing the work, and an index of them filled the prompt of every session
    (one project's ran to 263 lines against a 200-line load cap). The files stay where they
    are. Their index becomes `MEMORY.legacy.md`, and the new index opens with one line that
    says it is there and how far to trust it. Returns how many notes were set aside.

    Reversible by hand: rename the legacy index back and delete `.evidence`.
    """
    root = Path(root)
    marker = root / EVIDENCE_MARKER
    if not root.is_dir() or marker.exists():
        return 0
    index, legacy = root / INDEX_NAME, root / LEGACY_INDEX_NAME
    notes = [p for p in root.glob("*.md") if p.name not in RESERVED]
    count = 0
    if notes and index.is_file() and not legacy.exists():
        count = len(notes)
        index.replace(legacy)
        index.write_text(
            "# Memory Index\n\n"
            f"- [Notes from before memories carried evidence]({LEGACY_INDEX_NAME}) — {count} "
            "older notes, indexed in that file. They were saved with nothing to check them "
            "against, and many are stale or about other things: grep it when a task matches, "
            "and check a note against the code before relying on it.\n",
            encoding="utf-8",
            newline="\n",
        )
    marker.write_text(EVIDENCE_VERSION + "\n", encoding="utf-8")
    return count


__all__ = [
    "SIDECAR_NAME",
    "annotate",
    "digest",
    "entry_of",
    "forget",
    "mark_current",
    "note_read",
    "record",
    "retire_legacy_index",
    "stale",
]
