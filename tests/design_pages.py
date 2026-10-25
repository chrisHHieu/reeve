"""Pages built the way the guide tells a model to build them, for the design tests.

A project for a direction: its DESIGN.md (palette and fonts) and the kit's font folders copied
beside the pages, as the guide's copy command does. A page bound to it: the starting file with the
direction's palette and fonts in `:root` and its font links. A layout filled: every `[[slot]]`
replaced by its own words, so the page is as long and as varied as a filled one.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from harness.tools.design.directions import DIRECTIONS, Direction
from harness.tools.design.fonts import KIT_DIR
from harness.tools.design.layouts import SHELLS
from harness.tools.design.recipes import STARTER

_POINTS = "0,130 100,110 200,118 300,80 400,90 500,52 600,40"


def project_for(root: Path, direction_id: str) -> Direction:
    """`root` made a project in `direction_id`: DESIGN.md at its root, the fonts in design/fonts/."""
    direction = next(d for d in DIRECTIONS if d.id == direction_id)
    (root / "design" / "fonts").mkdir(parents=True, exist_ok=True)
    for slug in direction.fonts:
        target = root / "design" / "fonts" / slug
        if not target.exists():
            shutil.copytree(KIT_DIR / slug, target)
    palette = "\n".join(f"- {name}: {value}" for name, value in direction.palette.items())
    (root / "DESIGN.md").write_text(
        f"# {direction.name}\n\nColors\n{palette}\n\nType\n- display: {direction.display}\n"
        f"- body: {direction.body}\n- mono: {direction.mono}\n",
        encoding="utf-8",
    )
    return direction


def bind(direction: Direction, page: str = STARTER) -> str:
    """The starting file with `direction`'s palette, fonts and font links."""
    palette = ";".join(f"--{name}: {value}" for name, value in direction.palette.items()) + ";"
    page = re.sub(r"--ground: [^;]+;.*?--signal: [^;]+;", lambda _: palette, page, count=1, flags=re.S)
    page = re.sub(
        r"--font-display: [^;]+; --font-body: [^;]+;\s*--font-mono: [^;]+;",
        lambda _: f"--font-display: {direction.display}; --font-body: {direction.body}; --font-mono: {direction.mono};",
        page,
        count=1,
    )
    links = "".join(f'<link rel="stylesheet" href="fonts/{slug}/font.css">\n' for slug in direction.fonts)
    return re.sub(r'(<link rel="stylesheet" href="fonts/[^"]+">\n)+', lambda _: links, page, count=1)


def filled(layout: str) -> str:
    """`layout` with every slot replaced by its own description."""
    layout = layout.replace('points="[[x,y pairs]]"', f'points="{_POINTS}"')
    return re.sub(r"\[\[([^\]]+)\]\]", lambda m: m.group(1)[:1].upper() + m.group(1)[1:], layout)


def page_with(direction: Direction, kind: str, layout: str) -> str:
    """A whole page of `kind`: the bound starting file with the filled layout where it goes."""
    page = bind(direction)
    body = filled(layout)
    if kind in SHELLS:
        return re.sub(r"<body>.*</body>", lambda _: "<body>\n" + body + "\n</body>", page, count=1, flags=re.S)
    return re.sub(r'<main class="wrap">.*?</main>', lambda _: body, page, count=1, flags=re.S)
