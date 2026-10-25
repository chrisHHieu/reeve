"""What to say when a read names a path that is not a readable file.

`read_file` used to answer "not a file: <path>" for BOTH a directory and a path that does
not exist (7 of the real store's read errors, 2026-09-18..23: six missing files, one
drive root). The model cannot tell those apart from that sentence, and the recovery is
different — list the directory, or find the file's real name. So the answer says which
case it is and, for a missing file, names the closest siblings (the read tool
does the same), saving the `list_files` round-trip a guessed path otherwise costs.
"""

from __future__ import annotations

import difflib
from pathlib import Path

#: How many sibling names a "did you mean" line offers.
_SUGGESTIONS = 3
#: Directories wider than this are not scanned for suggestions: the hint is a courtesy,
#: and a read error must stay instant.
_SCAN_MAX = 2_000


def not_a_file(target: Path, shown: str) -> str:
    """The error text for a read of `target` (displayed as `shown`) that is not a file."""
    if target.is_dir():
        return f"{shown} is a directory, not a file — list it with list_files"
    return missing(target, shown, "file")


def missing(target: Path, shown: str, kind: str = "file") -> str:
    """`<kind> does not exist: <shown>`, plus the nearest existing folder or the closest
    sibling names. `list_files` on a guessed folder (7 of its 9 real failures since
    2026-09-20) gets the same help a guessed file does."""
    parent = target.parent
    while not parent.is_dir() and parent != parent.parent:
        parent = parent.parent
    message = f"{kind} does not exist: {shown}"
    if parent != target.parent:
        return f"{message} (its folder does not exist either; nearest existing: {parent})"
    near = _similar(parent, target.name)
    if near:
        # Spelled the way the model spelled the path, so a suggestion is copyable as-is.
        cut = max(shown.rfind("/"), shown.rfind("\\"))
        prefix = shown[: cut + 1] if cut >= 0 else ""
        message += " — did you mean: " + ", ".join(prefix + n for n in near)
    return message


def _similar(folder: Path, name: str) -> list[str]:
    try:
        names = []
        for index, entry in enumerate(folder.iterdir()):
            if index >= _SCAN_MAX:
                return []
            names.append(entry.name)
    except OSError:
        return []
    lowered = {n.lower(): n for n in names}
    picks = difflib.get_close_matches(name.lower(), list(lowered), n=_SUGGESTIONS, cutoff=0.6)
    stem = Path(name).stem.lower()
    # Same stem, other extension (`utils.ts` for `utils.tsx`) is the commonest near miss,
    # and difflib's ratio can rank it below an unrelated name of similar length.
    same_stem = [k for k in lowered if Path(k).stem == stem and k not in picks]
    ordered = (same_stem + picks)[:_SUGGESTIONS]
    return [lowered[k] for k in ordered]
