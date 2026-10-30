"""What a refused whole-file write shows the model: the file as it stands now.

The read guard (`observed.py`) refuses a whole-file write the model composed from a version it no
longer has: never read, read in part, changed on disk since, or folded out of its context. The
refusal used to end in "read it again", and the store showed what that cost (2026-09-25..10-30): 38
of 44 refusals were followed by a read of the same file, and 5 by a second refusal, because the model
read a window again and not the file (session 90e83048-f57: 30 lines of 181, then a second rewrite).

The refusal already knows which file is wanted, and that the whole of it is. So it shows the file,
numbered the reader's way, and the engine records it as read once the text reaches the model whole
(`loop/result_recording.py`). The next call is the write, written against what is on disk.

It shows only what the reader would have shown whole and unchanged in one call: UTF-8 text, within
the reader's line window and character budget, with no line past the reader's clip (a clipped line
rewritten whole would lose its tail). Anything else keeps the sentence, which says the whole file is
wanted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .observed import MAX_DIGEST_BYTES


def file_as_it_stands(path: Path, budget: int) -> Optional[tuple[str, str, int]]:
    """(numbered text, digest, line count) of the file now, or None when it cannot be shown whole.

    The digest is of the bytes the text was made from, so what is recorded as seen is the version
    shown even if the file changes before the refusal reaches the model."""
    # Imported here: the reader's package loads the whole file-tool surface, and the guard is wired
    # long before any of it is needed.
    from .files.window import DEFAULT_MAX_LINES, MAX_LINE_CHARS

    try:
        if not path.is_file() or path.stat().st_size > MAX_DIGEST_BYTES:
            return None
        raw = path.read_bytes()
    except (OSError, ValueError):
        return None
    if b"\x00" in raw:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    # The reader's own line splitting (a text-mode file: universal newlines, no trailing empty line).
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) > DEFAULT_MAX_LINES or any(len(line) > MAX_LINE_CHARS for line in lines):
        return None
    numbered = "\n".join(f"{number:>6}\t{line}" for number, line in enumerate(lines, 1))
    if len(numbered) > budget:
        return None
    return numbered, hashlib.sha256(raw).hexdigest(), len(lines)


__all__ = ["file_as_it_stands"]
