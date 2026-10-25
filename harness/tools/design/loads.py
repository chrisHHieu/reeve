"""What a design page asks to load and does not get.

A page that uses the font kit (`fonts.py`) links `fonts/<family>/font.css` and needs the folder
copied beside it. Forgotten, the page still opens, in a fallback face, and nothing says so: the
screenshots look plain and the model does not know why. The check says it instead: the local files
the page links that are not there (any file, not only fonts), and the kit's families the styles
name that no `@font-face` declares.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from .fonts import BY_NAME, PAGE_FONTS, copy_commands

#: How many missing files a report names.
_SHOWN = 6


def missing_files(urls: Iterable[str], page: Path) -> list[str]:
    """The `file:` URLs that failed, as paths relative to the page's folder."""
    names: list[str] = []
    for url in urls:
        raw = unquote(urlsplit(url).path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]  # file:///D:/x is D:/x
        try:
            name = Path(os.path.relpath(raw, page.parent)).as_posix()
        except ValueError:
            name = Path(raw).name
        if name not in names:
            names.append(name)
    return names[:_SHOWN]


def unloaded_fonts(used: Iterable[str], faces: Iterable[Any]) -> list[str]:
    """The kit families the page's styles name first that no `@font-face` on the page declares."""
    declared = {str(face).strip().lower() for face in faces}
    found: list[str] = []
    for name in used:
        family = BY_NAME.get(str(name).strip().lower())
        if family is not None and family.family.lower() not in declared and family.family not in found:
            found.append(family.family)
    return found


def lines(missing: list[str], unloaded: list[str]) -> list[str]:
    """What the model reads about them, one line each."""
    out: list[str] = []
    if missing:
        out.append("- The page links files that are not there: " + ", ".join(missing) + ".")
    if unloaded:
        slugs = [BY_NAME[name.lower()].slug for name in unloaded]
        out.append(
            f"- Named in the styles but never loaded, so a fallback face shows: {', '.join(unloaded)}. Link "
            + ", ".join(f"{PAGE_FONTS}/{slug}/font.css" for slug in slugs)
            + " and copy the folders beside the page ("
            + copy_commands(slugs)[0]
            + ")."
        )
    return out
