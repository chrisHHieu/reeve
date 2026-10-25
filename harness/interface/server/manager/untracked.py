"""What the Review tab may be handed for a file git has no diff for.

An untracked file has no `git diff`, so Review shows its CONTENT as additions. That content used to be
`read_text(errors="replace")` of whatever the file was: a 330 KB screenshot became 143,000 replacement
characters in 2,700 "lines", three of them in one workspace, and the GUI drew every one as a diff and
read them again each time the change list was read (65 requests for 4 files in 20 minutes, 585-813 KB
each). So the file is looked at first:

  - **binary** (a NUL in the first 8 KB, git's own rule): metadata only, never content;
  - **large text** (over `MAX_CONTENT_BYTES`): the first part, cut at a line, flagged `truncated`;
  - otherwise its content, as before.

The change LIST carries the same facts (`size`, `binary`, a line count for the additions, and a
`revision` that moves when the file does) so Review can keep a heavy card closed, and read a diff again
only when its file changed, instead of whenever the list was read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

#: Largest untracked text file shown whole as additions. Past it the diff is a preview of the start.
MAX_CONTENT_BYTES = 256 * 1024
#: Bytes read to decide whether a file is text: git looks at the same first 8,000.
SNIFF_BYTES = 8000
#: How many untracked files of a listing are looked at. `status -uall` lists every file of an untracked
#: folder (a `node_modules`), and each look is a stat and a read: past this they are listed as before.
MAX_INSPECTED = 300


def revision_of(path: Path) -> Optional[str]:
    """`size:mtime_ns`: changes whenever the file is written. None when it cannot be stat'ed (deleted)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _head(path: Path, limit: int) -> Optional[bytes]:
    try:
        with path.open("rb") as handle:
            return handle.read(limit)
    except OSError:
        return None


def inspect_untracked(path: Path) -> Optional[dict[str, Any]]:
    """`{size, binary, lines?, revision}` for the change list, or None when the file cannot be read.

    `lines` is the number of additions Review will show (counted only for text that fits whole); a
    text file that does not is given a size-based estimate so it is not mistaken for an empty change."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    head = _head(path, min(size, SNIFF_BYTES) or 1)
    if head is None:
        return None
    out: dict[str, Any] = {"size": size, "binary": b"\0" in head, "revision": revision_of(path)}
    if not out["binary"]:
        if size <= MAX_CONTENT_BYTES:
            data = _head(path, size) or b""
            out["lines"] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        else:
            out["lines"] = size // 40  # an estimate: long enough to keep the card closed
    return out


def untracked_content(path: Path) -> dict[str, Any]:
    """The `/changes/diff` body for an untracked file: its content, a bounded preview, or just facts."""
    facts = inspect_untracked(path)
    if facts is None:
        return {"untracked": True, "content": ""}
    if facts["binary"]:
        return {"untracked": True, "binary": True, "size": facts["size"]}
    raw = _head(path, MAX_CONTENT_BYTES + 1) or b""
    truncated = len(raw) > MAX_CONTENT_BYTES
    if truncated:
        raw = raw[:MAX_CONTENT_BYTES]
        cut = raw.rfind(b"\n")
        if cut > 0:
            raw = raw[: cut + 1]  # end on a whole line
    # Universal newlines, as `read_text` always gave: the diff view splits on "\n", and a CRLF file
    # (every file this machine's editors write) would otherwise carry a stray "\r" on each line.
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    out: dict[str, Any] = {"untracked": True, "content": text}
    if truncated:
        out.update(truncated=True, size=facts["size"])
    return out
