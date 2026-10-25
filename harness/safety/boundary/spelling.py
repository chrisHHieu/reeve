"""One spelling per path, so arguments naming the same file compare equal."""

from __future__ import annotations

import os
import re
import unicodedata

_GITBASH_DRIVE = re.compile(r"^/([a-zA-Z])/(.*)$")


def _is_absolute(path: str) -> bool:
    """Whether a normalised path is rooted, the same on every host. `os.path.isabs` cannot be
    used: on Windows it calls `/abs/a.txt` relative, so one batch would be judged one way where the
    tests run and another on CI. This and `normalise` together give every host the same answer,
    which a comparison behind a safety decision needs."""
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:", path))


def normalise(path: str) -> str:
    """One spelling for a path, so two arguments naming one file compare equal.

    Backslashes become `/` on both sides of `normpath`, because `normpath` depends on the platform:
    on Windows it turns `dir/a.txt` into `dir\\a.txt`, a prefix test written for `/` would then
    never match, and a write and a read of one file would look disjoint. Case is folded as well,
    since `a.txt` and `A.TXT` are one file on Windows.

    On Windows three more spellings are folded first: a Git-Bash drive alias (`/c/Users/x`), the
    extended-length prefixes (`\\\\?\\C:\\x`, `\\\\?\\UNC\\server\\share`) and Unicode
    normalization. Without the alias fold, `/c/Users/x` normalises to a drive-relative
    `\\c\\Users\\x` and a path inside the root looks outside it: a false ask, never a grant."""
    text = str(path).strip()
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        drive = _GITBASH_DRIVE.match(text.replace("\\", "/"))
        if drive:
            text = f"{drive.group(1).upper()}:/{drive.group(2)}"
        text = unicodedata.normalize("NFC", text)
    folded = os.path.normpath(text.replace("\\", "/"))
    return os.path.normcase(folded).replace("\\", "/")


def paths_overlap(left: str, right: str) -> bool:
    """Whether two paths can name the same file or one inside the other.

    Nesting counts both ways: reading a directory overlaps writing a file in it, and so does the
    reverse. A relative and an absolute path are not comparable, so a batch mixing them cannot be
    shown disjoint, and the caller treats "cannot be shown disjoint" as overlapping, the same
    fail-closed rule `write_paths` follows."""
    first, second = normalise(left), normalise(right)
    if not first or not second:
        return False
    if _is_absolute(first) != _is_absolute(second):
        # Reported as overlapping so the caller runs them one after another and does not guess:
        # a wrong guess is a race, while serialising only costs time.
        return True
    if first == second:
        return True
    return first.startswith(second + "/") or second.startswith(first + "/")
