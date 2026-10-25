"""Where the middle of a bounded output goes, so a truncation is not a re-run.

`tools/retention.py` keeps both ends of an oversized output and names the gap. That is
enough for the model to stop reasoning as if the text were whole — but not enough for
it to *recover*. The dropped middle is gone, and the only way back is to run the command
again. For a ten-minute build, discovering the truncation costs ten more minutes.

The escape hatch already existed and was unusable: the model can redirect to a file and
grep it. But truncation is discovered AFTER the process exits, and by then the output no
longer exists anywhere. This module removes the ordering problem by writing the complete
text at the moment of the cut, and handing back a path.

Two constraints shaped the design:

**The file has to live under a root.** Reading outside the session's roots is not
forbidden, but it falls out of every session grant (`permissions._under_root`), so each
read-back would raise an approval prompt. A recovery path that asks permission is not a
recovery path.

**Spilling may never break a tool call.** A full disk, a read-only root or a revoked
permission must degrade to plain truncation, which is exactly what happened before this
module existed. Every failure here is swallowed and reported as "no locator".
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

#: How many spill files a session keeps. Twenty truncations back is far past the point
#: where anyone is still asking about the first one.
DEFAULT_KEEP = 20

#: Total bytes the spill directory may hold. A single 5 MB log is the case this exists
#: for; a session that produces twelve of them should not silently eat a gigabyte.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

_SAFE_LABEL = re.compile(r"[^a-z0-9]+")
_SEQUENCE = re.compile(r"-(\d+)\.log$")


def _sequence(name: str) -> int:
    """The counter a spill file was named with; 0 for anything this store did not write."""
    found = _SEQUENCE.search(name)
    return int(found.group(1)) if found else 0


class SpillStore:
    """A bounded, self-pruning directory of complete tool outputs."""

    def __init__(
        self,
        directory: Path | str,
        *,
        keep: int = DEFAULT_KEEP,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.directory = Path(directory)
        self.keep = keep
        self.max_bytes = max_bytes
        # None until the first save, which resumes from the directory (`_resume`).
        self._counter: Optional[int] = None
        # Background shell tasks report from their own threads, so the counter that
        # names files is shared state.
        self._lock = threading.Lock()

    # -- construction -------------------------------------------------------------

    @classmethod
    def under(cls, roots: Iterable[Any], **kwargs: Any) -> Optional["SpillStore"]:
        """Place the store inside the session's own scratch space, or nowhere.

        A scratch root wins when there is one: it is already described to the model as
        the place for temporary files, and it is not the user's project. Otherwise the
        first writable root takes it. With no writable root at all the answer is None —
        a session that cannot write cannot spill, and pretending otherwise would put an
        error in front of the user on every long command.
        """
        writable = [r for r in roots or () if getattr(r, "writable", False)]
        if not writable:
            return None
        scratch = next((r for r in writable if getattr(r, "label", "") == "scratch"), None)
        home = scratch or writable[0]
        return cls(Path(home.path) / ".reeve" / "output", **kwargs)

    # -- the sink handed to retain() ----------------------------------------------

    def sink(self, label: str) -> Callable[[str], Optional[str]]:
        """A one-argument callable for `retain(..., spill=...)`.

        Bound once at tool-construction time rather than per call: the retainer runs on
        every command, and only the truncating ones ever invoke it.
        """

        def save(text: str) -> Optional[str]:
            return self.save(text, label=label)

        return save

    # -- writing ------------------------------------------------------------------

    def save(self, text: str, *, label: str = "output") -> Optional[str]:
        """Write `text` whole and return a path the file tools can read, or None.

        Returns None for every failure. The caller is in the middle of reporting a
        tool result and has nothing useful to do with a disk error — the output is
        merely as lost as it was before this module existed.
        """
        try:
            with self._lock:
                if self._counter is None:
                    self._counter = self._resume()
                self._counter += 1
                name = f"{_SAFE_LABEL.sub('-', label.lower()).strip('-') or 'output'}-{self._counter}.log"
            path = self.directory / name
            self._prepare()
            # Written whole and in one call: a partial spill is worse than none, because
            # the locator would promise a completeness the file does not have. Created
            # exclusively (never through a pre-existing path): the directory is
            # predictable, so a planted symlink could otherwise redirect this write onto
            # a file the agent was never allowed to touch (the spill-safety rule,
            # adopted 2026-09-23). An existing REGULAR file is unlinked first — a name
            # can still be taken by something this store did not write.
            _write_exclusive(path, text)
            self._prune(keeping=path)
            return str(path)
        except OSError:
            return None

    def _resume(self) -> int:
        """The highest sequence already in the directory; the next file is numbered after it.

        A store is rebuilt with its engine (the app restarts, a session is reopened) over a
        directory an earlier one filled, and the transcript still cites those files: "full
        output: …/page-2.log". Counting from zero wrote the next page over one of them, so
        the path a tool result named now held a DIFFERENT result. Real store, 2026-10-04:
        9 of 99 cited spill paths were later reused, in 3 of the 13 sessions that spilled;
        in one, a 103,000-character paper became a 2,000-character page about something
        else. A file that pruning removed reads as missing, which is true. A file that was
        replaced reads as an answer.
        """
        try:
            return max((_sequence(p.name) for p in self.directory.iterdir()), default=0)
        except OSError:
            return 0  # no directory yet: nothing to collide with

    def _prepare(self) -> None:
        """Create the directory, and make git ignore it.

        The store usually lands inside the user's own project. A tool that adds
        untracked files to someone's `git status` every time a build log runs long has
        made itself their problem; the ignore file is written once, next to the store.
        """
        existed = self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True)
        if existed:
            return
        try:
            (self.directory.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        except OSError:
            pass  # cosmetic only — never worth failing a spill over

    def _prune(self, *, keeping: Path) -> None:
        """Drop the oldest files until both bounds hold. The file just written stays."""
        try:
            entries = [
                (p, p.stat())
                for p in self.directory.iterdir()
                if p.is_file() and p.suffix == ".log"
            ]
        except OSError:
            return
        # Newest first. Several spills inside one filesystem timestamp tick is ordinary
        # — a loop of failing commands does it — and mtime alone would then order them
        # arbitrarily and evict a newer file ahead of an older one. The sequence number
        # in the name is the store's own monotonic clock, so it breaks every tie.
        entries.sort(key=lambda e: (e[1].st_mtime, _sequence(e[0].name)), reverse=True)
        total = 0
        for index, (path, info) in enumerate(entries):
            total += info.st_size
            over = index >= self.keep or (total > self.max_bytes and index > 0)
            if over and path != keeping:
                try:
                    os.unlink(path)
                except OSError:
                    pass


__all__ = ["SpillStore", "DEFAULT_KEEP", "DEFAULT_MAX_BYTES"]


def _write_exclusive(path: Path, text: str) -> None:
    """Create `path` with `O_EXCL` and write `text` whole.

    A pre-existing path is handled by unlinking it first and retrying — and only when it
    is a regular file: a symlink here is either an accident or a planted redirect, and
    following it would put the spill's content somewhere the session never chose.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise OSError(f"refusing to overwrite a non-regular file: {path}")
        path.unlink()
        descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
