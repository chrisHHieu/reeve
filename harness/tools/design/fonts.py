"""The fonts Reeve ships for design pages.

A design page works offline, so until now its fonts were system stacks, and on Windows every
direction came out in Segoe UI, Georgia or Arial: the layout and colours right, the page plain
(owner, 2026-10-19, beside OpenDesign's serif headline). A face with character is most of what
makes a first draft look designed. These nine families come with Reeve
(`harness/tools/design/fonts/<slug>/`), from Fontsource, under the SIL Open Font License, each
with Latin, Latin Extended and Vietnamese, at the weights the directions use, as woff2.

A page uses a family by having it beside it: the folder is copied into the page's `fonts/` folder
(`design/fonts/newsreader/`) and its `font.css` linked. The copy is the model's, with its shell,
so it passes the same permission as any other write; the guide hands it the command. The check
says when a page links a file that is not there, or names a family of the kit that it never
loads, so a forgotten copy is caught after the write. An export carries the files with the page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Where the kit is, in the source tree and in the built sidecar alike (`collect_data_files`).
KIT_DIR = Path(__file__).resolve().parent / "fonts"
#: Where a page keeps the families it uses, beside it.
PAGE_FONTS = "fonts"


@dataclass(frozen=True)
class KitFamily:
    slug: str
    family: str
    kind: str
    character: str
    weights: str


KIT: tuple[KitFamily, ...] = (
    KitFamily("plus-jakarta-sans", "Plus Jakarta Sans", "sans display", "geometric and crisp, product headlines", "500–800"),
    KitFamily("be-vietnam-pro", "Be Vietnam Pro", "sans text", "a clear reading face made for Vietnamese and Latin alike", "400–700"),
    KitFamily("newsreader", "Newsreader", "serif display", "editorial, with a fine italic for emphasis", "400–600, italic 400–500"),
    KitFamily("lora", "Lora", "serif display", "soft, calligraphic, warm", "400–600, italic 400"),
    KitFamily("space-grotesk", "Space Grotesk", "sans display", "technical, a little quirky", "400–700"),
    KitFamily("ibm-plex-sans", "IBM Plex Sans", "sans text", "engineered, dense tables and dashboards", "400–600"),
    KitFamily("jetbrains-mono", "JetBrains Mono", "mono", "code, ids, numbers, metadata", "400–500"),
    KitFamily("bricolage-grotesque", "Bricolage Grotesque", "sans display", "bold and expressive at large sizes", "500–800"),
    KitFamily("archivo", "Archivo", "sans", "neutral grotesque for grids, one family for everything", "400–800"),
)

BY_SLUG = {family.slug: family for family in KIT}
#: The kit's families by their CSS name, lower-cased (what a stylesheet's font-family says).
BY_NAME = {family.family.lower(): family for family in KIT}


def link_tag(slug: str) -> str:
    return f'<link rel="stylesheet" href="{PAGE_FONTS}/{slug}/font.css">'


def copy_commands(slugs: list[str]) -> list[str]:
    """The shell lines that put `slugs` beside a page in `design/`: PowerShell, then POSIX."""
    if os.name == "nt":
        sources = ", ".join(f"'{KIT_DIR / slug}'" for slug in slugs)
        posix = " ".join(f"'{(KIT_DIR / slug).as_posix()}'" for slug in slugs)
        return [
            "PowerShell: New-Item -ItemType Directory -Force design/fonts | Out-Null; "
            f"Copy-Item -Recurse -Force {sources} design/fonts/",
            f"Git Bash: mkdir -p design/fonts && cp -R {posix} design/fonts/",
        ]
    sources = " ".join(f"'{KIT_DIR / slug}'" for slug in slugs)
    return [f"mkdir -p design/fonts && cp -R {sources} design/fonts/"]


def kit_text() -> str:
    """The kit, written out for the guide: what is there, how to bring a family in, how to link it."""
    rows = "\n".join(f"- {f.family} [{f.slug}]: {f.kind}, {f.character}; weights {f.weights}." for f in KIT)
    every = [f.slug for f in KIT]
    commands = "\n".join(f"  {line}" for line in copy_commands(every[:2]))
    return (
        "Fonts that come with Reeve, each with Vietnamese. Use them instead of system fonts; a DESIGN.md that "
        "names other fonts wins:\n"
        f"{rows}\n"
        "To use a family, copy its folder beside the page and link its font.css in <head>, before your <style>:\n"
        f"  {link_tag('newsreader')}\n"
        f"Copy with your shell before the first write (example for two families; name the ones you use):\n{commands}\n"
        "Then the page's font stacks name the family first with a system fallback after it, for example\n"
        "  --font-display: 'Newsreader', Georgia, serif; and DESIGN.md names the families it uses.\n"
        "The check says if a linked file is missing or a family is named but never loaded."
    )


def kit_families() -> list[Path]:
    """The kit's family folders that are present (all of them in a whole install)."""
    return [KIT_DIR / f.slug for f in KIT if (KIT_DIR / f.slug / "font.css").is_file()]
