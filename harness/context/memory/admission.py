"""One memory directory's door: judge a file a model wrote, and record what an admitted one
stands on.

Both ways a model writes go through this object — the file tools (`tools/memory_admission.py`,
which judges the file a call left behind) and the `auto_memory` tool (`AutoMemory.admit`) — so
the two cannot drift into two sets of rules.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import anchors as _anchors
from . import memory_format as _fmt
from .gate import (
    INDEX_NAME,
    PROGRESS_NAME,
    RESERVED,
    Refusal,
    anchors_of,
    judge,
    judge_index,
    judge_progress,
    kind_of,
)

#: What a door already settled, process-wide: a resolved path and the digest of the text it
#: admitted, or that the person (or the harness) wrote through `AutoMemory`. A shell call's
#: check of the whole directory (`tools/memory_admission.py`) leaves such a file alone: it
#: changed during the call, but another door had already judged it — another session's
#: write, a child's, a save in Settings — and judging it again with this session's evidence
#: would put back a memory that was admitted with its own.
_SETTLED: dict[str, str] = {}
_SETTLED_LOCK = threading.Lock()


def _text_digest(text: str) -> str:
    # Line ends as `read_text` gives them back, so a file written with `\r\n` matches.
    plain = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(plain.encode("utf-8", "surrogatepass")).hexdigest()


def settle(path: Path, text: str) -> None:
    """Note that `path` now holds `text` through a door that judged it, or needs none."""
    with _SETTLED_LOCK:
        _SETTLED[str(Path(path).resolve())] = _text_digest(text)


def settled(path: Path, text: str) -> bool:
    """Whether `path` holds exactly what a door last settled it to."""
    with _SETTLED_LOCK:
        return _SETTLED.get(str(Path(path).resolve())) == _text_digest(text)


#: A path a note names in its text: a backticked token with a directory or an extension.
_NAMED_RE = re.compile(r"`([^`\s]+)`")
#: At most this many named files are recorded for one note.
MAX_NAMED = 8


def named_files(body: str, anchor: Callable[[str], Optional[Path]], skip: Sequence[Path] = ()) -> list[tuple[str, Path]]:
    """The files of the session's folders that a note's text names in backticks.

    A model is not asked to declare what its note stands on: most name the file they are
    about in passing, and that is enough for the harness to tell a later session when it
    changed. Only a token that resolves to a file counts, so a command, a flag or a made-up
    path records nothing."""
    found: list[tuple[str, Path]] = []
    seen = {Path(path) for path in skip}
    for token in _NAMED_RE.findall(body or ""):
        raw = token.strip().rstrip(".,:;)")
        if not re.match(r"^[A-Za-z]:[\\/]", raw):
            raw = raw.split(":", 1)[0]  # `a.py:42` names a.py; a drive letter keeps its colon
        if "/" not in raw and "\\" not in raw and "." not in raw:
            continue
        at = anchor(raw)
        if at is None or not at.is_file() or at in seen:
            continue
        seen.add(at)
        found.append((raw, at))
        if len(found) >= MAX_NAMED:
            break
    return found


class Admission:
    """`anchor(raw)` is the path in one of the session's own folders that `raw` names, or
    None; `stamp()` is `(session id, turn)`; `unsettled()` is the engine's word on whether
    the turn's work is checked (`_work_unsettled`); `external()` is whether the turn read web
    or connector content."""

    def __init__(
        self,
        root: Path,
        *,
        anchor: Callable[[str], Optional[Path]],
        stamp: Callable[[], tuple[str, Optional[int]]] = lambda: ("", None),
        unsettled: Callable[[], str] = lambda: "",
        external: Callable[[], bool] = lambda: False,
    ) -> None:
        self.root = Path(root)
        self._anchor, self._stamp = anchor, stamp
        self._unsettled, self._external = unsettled, external

    def verdict(self, name: str, text: str, before: str = "") -> Optional[Refusal]:
        """Why `name` may not read `text`, or None. `before` is what it held."""
        if name == INDEX_NAME:
            return judge_index(text, names={p.name for p in self.root.glob("*.md")})
        if name == PROGRESS_NAME:
            return judge_progress(text, before=before, unsettled=self._unsettled())
        if name in RESERVED:
            return None
        index = self.root / INDEX_NAME
        return judge(
            name,
            text,
            anchor=self._anchor,
            index_text=index.read_text(encoding="utf-8", errors="replace") if index.is_file() else "",
        )

    def record(self, name: str, text: str) -> None:
        """Note what an admitted file was saved with (`anchors.record`)."""
        settle(self.root / name, text)
        if name in RESERVED and name != PROGRESS_NAME:
            return
        session, turn = self._stamp()
        if name == PROGRESS_NAME:
            _anchors.record(self.root, name, kind="progress", anchors=[], session=session, turn=turn)
            return
        meta, body = _fmt.split(text)
        declared = [(raw, at) for raw in anchors_of(meta) if (at := self._anchor(raw)) is not None]
        _anchors.record(
            self.root,
            name,
            kind=kind_of(meta),
            anchors=declared,
            named=named_files(body, self._anchor, skip=[at for _, at in declared]),
            session=session,
            turn=turn,
            external=bool(self._external()),
        )

    def admit(self, name: str, text: str) -> Optional[Refusal]:
        """`verdict`, recording when the answer is yes (`AutoMemory.admit`: the file on disk
        still holds what it held before)."""
        path = self.root / name
        before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        refusal = self.verdict(name, text, before)
        if refusal is None:
            try:
                self.record(name, text)
            except OSError:
                pass  # the record is bookkeeping: a memory is not refused for it
        return refusal

    def read(self, name: str) -> None:
        """Count a read by the model of a file the harness has a record of (`anchors.note_read`)."""
        _anchors.note_read(self.root, name)

    def forget(self, name: str) -> None:
        _anchors.forget(self.root, name)


def project_anchor(roots: Sequence[Path], workspace: Optional[Path]) -> Callable[[str], Optional[Path]]:
    """The resolver a session's memories are anchored with: a path names evidence when it
    exists inside one of the session's own folders (never the memory directory itself)."""
    bases = [Path(root).resolve() for root in roots]
    first = [Path(workspace).resolve()] if workspace is not None else []

    def anchor(raw: str) -> Optional[Path]:
        candidate = Path(str(raw).strip()).expanduser()
        tries = [candidate] if candidate.is_absolute() else [base / candidate for base in [*first, *bases]]
        for path in tries:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.exists() and any(resolved == base or base in resolved.parents for base in bases):
                return resolved
        return None

    return anchor


__all__ = ["Admission", "named_files", "project_anchor", "settle", "settled"]
