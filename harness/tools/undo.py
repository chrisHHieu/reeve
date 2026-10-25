"""Before-images, so a write the agent made can be taken back.

Approval asks a person to PREDICT: read the proposed action, imagine what it does, decide.
Someone who does not write software cannot do that for `apply_patch`, so they approve
everything, and the gate that was supposed to protect them protects nobody. Undo asks them
to RECOGNISE instead — look at what happened, say no — which anyone can do. It is the
mechanism that makes full capability safe without taking any capability away.

The prior content has to be captured BEFORE the tool runs, because afterwards it does not
exist anywhere: `provenance.SessionFiles` records that the agent touched a path and with
what origin, never what was there first. This is the piece that was missing, and it is the
same shape as `tools/spill.py` — a bounded, self-pruning directory under one of the
session's roots — for the same reason: something is about to be lost, and the only moment
it can be kept is now.

Three properties decide whether this is trustworthy:

**A file that did not exist is not an empty file.** Undoing a create must DELETE, not write
zero bytes. Getting this backwards leaves debris that looks like the user's own work.

**A snapshot may never block a write.** A full disk, an unreadable file, a path outside the
roots — all degrade to "not undoable", which is exactly today's behaviour. Refusing to act
because the safety net failed would be a new way to fail that nobody asked for.

**An entry is only offered while its file is really there.** Pruning is bounded, so old
snapshots go; an offer to undo something that cannot be undone is worse than no offer.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

#: How many write steps stay undoable. Far more than anyone scrolls back through, and
#: small enough that a session cannot fill a disk with copies.
DEFAULT_KEEP = 40

#: Total bytes of before-images kept.
DEFAULT_MAX_BYTES = 128 * 1024 * 1024

#: Files larger than this are not copied. A source file is kilobytes; something bigger is
#: a database or a build artifact, and spending a hundred megabytes to make one write
#: reversible is a trade nobody asked for. The step is simply not undoable, and says so.
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024


def _creatable(target: Path) -> bool:
    """Whether a write could bring this path into existence.

    False when an ancestor is a regular file — `a/b.txt/c.py` is not a path any tool can
    create, so it must not be recorded as an undoable create.
    """
    for parent in target.parents:
        if parent.is_dir():
            return True
        if parent.exists():
            return False  # exists but is not a directory
    return False


@dataclass(frozen=True)
class Snapshot:
    """One file, as it stood before a tool touched it."""

    path: str
    #: Where the copy lives. Empty when the file did not exist — there was nothing to copy.
    saved_to: str
    #: Whether the file existed before the call. False means undo DELETES it.
    existed: bool

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "saved_to": self.saved_to, "existed": self.existed}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Snapshot":
        return cls(
            path=str(raw.get("path", "")),
            saved_to=str(raw.get("saved_to", "")),
            existed=bool(raw.get("existed")),
        )


class UndoStore:
    """Before-images for one session's writes, keyed by the call that made them."""

    def __init__(
        self,
        directory: Path | str,
        *,
        keep: int = DEFAULT_KEEP,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        base: Optional[Path] = None,
    ) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        if max_bytes < 1 or max_file_bytes < 1:
            raise ValueError("byte budgets must be positive")
        self.directory = Path(directory)
        # The workspace a RELATIVE path hangs off. Without it this store refused every
        # relative write — and a model writes `notes.txt`, not `C:/…/notes.txt`, so undo
        # silently did nothing for the ordinary case. Found by running one real turn;
        # every test until then had passed absolute paths (`tmp_path / "x"`).
        self.base = Path(base) if base is not None else None
        self.keep = keep
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self._counter = 0
        self._lock = threading.Lock()
        # call_id → the files that call was about to change. Written to disk as well, so a
        # restart does not silently drop the ability to take back what it can still see.
        self._index: "dict[str, list[Snapshot]]" = {}
        self._load()

    # -- construction -------------------------------------------------------------

    @classmethod
    def under(cls, roots: Iterable[Any], **kwargs: Any) -> Optional["UndoStore"]:
        """Beside the session's other machine-facing files, or nowhere.

        Deliberately NOT the scratch root that `SpillStore.under` prefers. A spill is
        disposable; a before-image is the only copy of the user's file, so it belongs on
        the same volume as the original — a scratch dir on another disk turns undo into a
        cross-device copy that can fail exactly when it matters.
        """
        writable = [r for r in roots or () if getattr(r, "writable", False)]
        if not writable:
            return None
        home = next(
            (r for r in writable if getattr(r, "label", "") != "scratch"), writable[0]
        )
        return cls(Path(home.path) / ".reeve" / "undo", **kwargs)

    # -- capture ------------------------------------------------------------------

    def snapshot(self, call_id: str, paths: Iterable[str]) -> list[Snapshot]:
        """Copy each path's current content aside. Returns what became undoable.

        Never raises. A path that cannot be read, is too large, or is a directory simply
        does not become undoable — the write proceeds either way, because a backup that
        failed is not a reason to refuse the work.
        """
        taken: list[Snapshot] = []
        for raw in paths:
            shot = self._capture(raw)
            if shot is not None:
                taken.append(shot)
        if taken:
            self._index[call_id] = taken
            self._prune()
            self._save()
        return taken

    def _capture(self, raw: str) -> Optional[Snapshot]:
        try:
            target = Path(raw).expanduser()
            if not target.is_absolute():
                # Relative paths hang off the session's workspace — the same rule the
                # permission engine scopes by (`provenance.resolve`). Resolving against
                # this PROCESS's cwd would snapshot the wrong file, so with no workspace
                # to anchor to, refusing is still the honest answer.
                if self.base is None:
                    return None
                target = (self.base / target).resolve()
            if target.is_dir():
                return None
            if not target.exists():
                # Undoing a create means deleting it. There is nothing to copy, but the
                # step is still undoable — arguably the case that matters most, since a
                # file that appeared is the one a person most wants gone.
                #
                # Only when the path could actually come into being, though: a target
                # under a regular file can never be created, so promising to delete it
                # later is an offer that would do nothing when taken up.
                if not _creatable(target):
                    return None
                return Snapshot(path=str(target), saved_to="", existed=False)
            if target.stat().st_size > self.max_file_bytes:
                return None
            with self._lock:
                self._counter += 1
                name = f"{self._counter}-{target.name}"
            self._prepare()
            copy = self.directory / name
            shutil.copy2(target, copy)
            return Snapshot(path=str(target), saved_to=str(copy), existed=True)
        except (OSError, ValueError):
            return None

    # -- restore ------------------------------------------------------------------

    def undoable(self, call_id: str) -> bool:
        """Whether this call can still be taken back — checked against the disk, not the
        index. Pruning is bounded, so an entry outlives its file."""
        return all(
            not s.existed or Path(s.saved_to).is_file()
            for s in self._index.get(call_id, [])
        ) and bool(self._index.get(call_id))

    def images(self, call_id: str) -> list[Snapshot]:
        """The before-images of one call still on disk: each file as it stood before the
        call (a file that did not exist has no copy, and is still an image of that)."""
        return [
            shot for shot in self._index.get(call_id, [])
            if not shot.existed or Path(shot.saved_to).is_file()
        ]

    def undoable_calls(self) -> list[str]:
        """Every call this store can still take back, each checked like `undoable`. A
        surface rebuilt from the transcript has no other way to know: the transcript records
        what a call did, not whether its before-image survived."""
        return [call_id for call_id in list(self._index) if self.undoable(call_id)]

    def restore(self, call_id: str) -> dict[str, Any]:
        """Put every file this call touched back the way it was.

        All-or-nothing is deliberately NOT attempted: a partial restore is reported
        honestly rather than rolled back, because rolling back a rollback needs a second
        before-image and the user is better served by knowing exactly what happened.
        """
        shots = self._index.get(call_id)
        if not shots:
            return {"ok": False, "error": "nothing recorded for this step"}
        restored: list[str] = []
        failed: list[str] = []
        for shot in shots:
            try:
                target = Path(shot.path)
                if shot.existed:
                    if not Path(shot.saved_to).is_file():
                        failed.append(shot.path)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(shot.saved_to, target)
                elif target.exists():
                    target.unlink()
                restored.append(shot.path)
            except OSError:
                failed.append(shot.path)
        if restored and not failed:
            # Consumed: the before-image now describes a state that is current again, so
            # offering the same undo twice would restore stale content over newer work.
            self._index.pop(call_id, None)
            self._save()
        return {
            "ok": bool(restored) and not failed,
            "restored": restored,
            **({"failed": failed} if failed else {}),
        }

    # -- storage ------------------------------------------------------------------

    @property
    def _index_file(self) -> Path:
        return self.directory / "index.json"

    def _prepare(self) -> None:
        existed = self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True)
        if existed:
            return
        try:
            (self.directory.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> None:
        try:
            raw = json.loads(self._index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for call_id, shots in raw.get("calls", {}).items():
            if isinstance(shots, list):
                self._index[str(call_id)] = [
                    Snapshot.from_dict(s) for s in shots if isinstance(s, dict)
                ]
        self._counter = int(raw.get("counter", 0) or 0)

    def _save(self) -> None:
        try:
            self._prepare()
            self._index_file.write_text(
                json.dumps(
                    {
                        "counter": self._counter,
                        "calls": {
                            k: [s.as_dict() for s in v] for k, v in self._index.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # an unwritable index costs undo across a restart, never the write

    def _prune(self) -> None:
        """Drop the oldest entries until both bounds hold, newest kept.

        Insertion order is the truth here: dicts preserve it, and it is the order the
        calls actually happened in — unlike mtime, which several copies inside one
        filesystem tick would leave ambiguous.
        """
        while len(self._index) > self.keep:
            self._forget(next(iter(self._index)))
        while self._total_bytes() > self.max_bytes and len(self._index) > 1:
            self._forget(next(iter(self._index)))

    def _total_bytes(self) -> int:
        total = 0
        for shots in self._index.values():
            for shot in shots:
                try:
                    total += Path(shot.saved_to).stat().st_size if shot.existed else 0
                except OSError:
                    continue
        return total

    def _forget(self, call_id: str) -> None:
        for shot in self._index.pop(call_id, []):
            if not shot.existed:
                continue
            try:
                os.unlink(shot.saved_to)
            except OSError:
                pass


__all__ = ["UndoStore", "Snapshot", "DEFAULT_KEEP", "DEFAULT_MAX_BYTES"]


# -- the pipeline listener ---------------------------------------------------------

def snapshot_listener(store: UndoStore, locate: Any) -> Any:
    """A `tools/pre-execute` listener that captures before-images.

    `locate(name, arguments) -> (paths, located)` is injected rather than imported: it
    lives in the permission layer, and a tool module that reached into permissions would
    invert the dependency for one lookup.

    NOTE ON FAILURE POLICY. `ToolPipeline._run_pre` fails CLOSED — a pre-execute listener
    that raises blocks the call, because a gate that crashed cannot be read as consent.
    This listener is NOT a gate, so it must not inherit that: a backup that failed is no
    reason to refuse the work. It swallows everything and lets the call through, leaving
    the step simply not undoable.
    """

    async def listener(execution: Any, nxt: Any) -> Any:
        try:
            paths, located = locate(execution.name, execution.arguments or {})
            if located and paths:
                store.snapshot(execution.call_id, paths)
        except Exception:  # noqa: BLE001 — see the failure-policy note above
            pass
        return await nxt()

    return listener
