"""What git says is different in a workspace, for the files a turn changed without naming them.

`changes.py` builds the turn's receipt from the paths a write tool names. A shell command or a
program names none — `sed -i` across nine files, a formatter, a codegen script — and those
turns used to end with no receipt at all, the one case where a person most needs one (GUI
rendering review, G4). Git already knows which files differ; this module asks it, twice: once
before the turn's first command runs, and once at the end.

Same two rules as the receipt it feeds:

- **It reports what happened, never guesses.** Outside a git work tree there is no honest
  before-image, so there is no answer — `None`, not an empty dict.
- **It never blocks.** Every git call is bounded and every failure is `None`; a receipt that
  could not be made is the old behaviour, a missing line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from harness.platform.spawn import run as spawn_run

_TIMEOUT_S = 5.0

#: Past this many changed paths the snapshot is not taken: a tree that dirty (an unignored
#: build output, a vendored folder) would cost seconds per turn to hash, and a receipt that
#: lists two thousand files has stopped being something a person reads.
_MAX_ENTRIES = 2_000

#: The before-image of one file is kept only up to the receipt's own diff cap, and the whole
#: snapshot within a budget: past either, the file is still reported, just without counts.
_MAX_FILE_BYTES = 262_144
_MAX_TOTAL_BYTES = 16 * 1024 * 1024

#: Folders a command fills as a side effect of running — interpreter and tool caches, and
#: installed dependencies. Not the command's change to the person's files: `pytest` in a repo
#: with no .gitignore put three `__pycache__/*.pyc` on the receipt (live run, 2026-09-26).
#: Build outputs (`dist`, `build`, `target`) are NOT here — a build may be exactly what the
#: person asked for.
_GENERATED = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        ".turbo",
        ".parcel-cache",
    }
)


@dataclass(frozen=True)
class Entry:
    """One path git reported as different from HEAD, as it stood when the snapshot was taken."""

    code: str  # the porcelain `XY`
    signature: Optional[tuple[int, int]]  # (size, mtime_ns); None when the file is gone
    text: Optional[str]  # its content then, when small enough to keep
    kept: bool  # whether `text` is the real before-image (False: too big, unreadable, gone)


@dataclass(frozen=True)
class Change:
    """One path whose state moved between two snapshots."""

    path: str
    kind: str  # "created" | "modified" | "deleted"
    before: Optional[str]  # its content before, when known
    known: bool  # whether `before` can be diffed against (a creation's is known and empty)


def _git(root: Path, *args: str) -> Optional[bytes]:
    try:
        done = spawn_run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=_TIMEOUT_S,
        )
    except Exception:  # missing git, a timeout, a bad cwd: all mean "no answer"
        return None
    return done.stdout if done.returncode == 0 else None


def _signature(path: Path) -> Optional[tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _read(path: Path, limit: int) -> Optional[str]:
    try:
        if path.stat().st_size > limit:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return None if "\x00" in text else text  # a binary file has no lines to count


@dataclass(frozen=True)
class Snapshot:
    root: Path
    prefix: str  # the root's place inside the repository, "" at the top, else "sub/dir/"
    entries: dict[str, Entry]  # keyed by the path relative to `root`, forward slashes


def take(root: Optional[str | Path]) -> Optional[Snapshot]:
    """Every path under `root` that git reports as different from HEAD, or None when `root`
    is not inside a git work tree, git is missing, or the answer would be too large."""
    if not root:
        return None
    base = Path(root)
    prefix_raw = _git(base, "rev-parse", "--show-prefix")
    if prefix_raw is None:
        return None
    prefix = prefix_raw.decode("utf-8", errors="replace").strip()
    # `-z` keeps paths raw (no quoting), `--no-renames` keeps one path per entry, and the
    # `.` pathspec keeps the answer to this folder even when the repository is larger.
    # Porcelain paths are relative to the REPOSITORY top, so the prefix is taken off below.
    raw = _git(
        base,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        "--",
        ".",
        # Reeve's own working folder (the intent journal) is not the command's doing.
        ":(exclude).reeve",
    )
    if raw is None:
        return None
    records = [r for r in raw.decode("utf-8", errors="surrogateescape").split("\0") if r]
    if len(records) > _MAX_ENTRIES:
        return None
    entries: dict[str, Entry] = {}
    budget = _MAX_TOTAL_BYTES
    for record in records:
        if len(record) < 4:
            continue
        code, rel = record[:2], record[3:]
        if prefix and rel.startswith(prefix):
            rel = rel[len(prefix) :]
        if any(part in _GENERATED for part in rel.split("/")[:-1]):
            continue
        full = base / rel
        signature = _signature(full)
        text = None
        if signature is not None and signature[0] <= min(_MAX_FILE_BYTES, budget):
            text = _read(full, _MAX_FILE_BYTES)
            if text is not None:
                budget -= signature[0]
        entries[rel] = Entry(code=code, signature=signature, text=text, kept=text is not None)
    return Snapshot(root=base, prefix=prefix, entries=entries)


def _head_text(snapshot: Snapshot, rel: str) -> Optional[str]:
    """The committed content of a file that was clean when the first snapshot was taken."""
    blob = _git(snapshot.root, "cat-file", "-p", f"HEAD:{snapshot.prefix}{rel}")
    if blob is None or len(blob) > _MAX_FILE_BYTES or b"\x00" in blob:
        return None
    return blob.decode("utf-8", errors="replace")


def changes(before: Snapshot, after: Snapshot) -> list[Change]:
    """The paths whose state moved between two snapshots of the same root.

    Three ways a path moves, and each has its own before-image:

    - dirty only AFTER: it was at HEAD, so HEAD is the before (or nothing, if untracked);
    - dirty in BOTH, with a different size or mtime: the first snapshot's copy is the before;
    - dirty only BEFORE: it went back to HEAD — a `git checkout -- file` that threw away work,
      which is exactly the kind of change a person must see on the receipt.
    """
    out: list[Change] = []
    for rel in sorted(set(before.entries) | set(after.entries)):
        was = before.entries.get(rel)
        now = after.entries.get(rel)
        path = str(before.root / rel)
        if now is not None and now.signature is None:
            # Gone from disk. Only a change if it was on disk when the turn began.
            if was is None or was.signature is not None:
                prior = was.text if was is not None else _head_text(before, rel)
                out.append(Change(path, "deleted", prior, prior is not None))
            continue
        if was is None:
            if now is None:
                continue
            if now.code == "??":
                out.append(Change(path, "created", None, True))
            else:
                prior = _head_text(before, rel)
                out.append(Change(path, "modified", prior, prior is not None))
            continue
        if now is not None and now.signature == was.signature:
            continue
        if now is None and was.signature is not None and _signature(before.root / rel) == was.signature:
            # Clean now, and the file on disk is the one the turn found: HEAD moved to it (a
            # commit), the file did not move. A turn that only committed showed every committed
            # file as changed (owner's screenshot, 2026-10-15: "19 files changed").
            continue
        if was.signature is None:
            # Deleted before the turn and back now.
            out.append(Change(path, "created", None, True))
            continue
        out.append(Change(path, "modified", was.text, was.kept))
    return out


def same_path(a: str, b: str) -> bool:
    """Whether two spellings name one file — case and separators differ on Windows."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
