"""Which written files are design pages: what the engine checks after a write and Designs lists.

A page under a `design/` folder, and only that. For a day (2026-10-19) any whole HTML page that
opened by itself counted too, after a session wrote its pricing pages to a folder of its own; then a
flow diagram drawn as HTML, which nobody had asked to have designed, came with a design's check, a
card on Designs and the design tools in Files (owner, 2026-10-20). Whether a page is a design is the
request's, and the prompt says where a design goes; a page elsewhere is a file.
"""

from __future__ import annotations

from pathlib import Path

#: The folder a design is written to; a page in it is a design whatever it holds.
DESIGN_DIR = "design"
_HTML = frozenset({".html", ".htm"})
#: Folders whose pages are someone else's, or generated: never designs.
SKIP_DIRS = frozenset(
    {
        "node_modules", "dist", "build", "out", "target", "vendor", "coverage", "htmlcov",
        "site-packages", "venv", ".venv", "__pycache__", ".git", ".next", ".nuxt", ".svelte-kit",
    }
)


def is_design_file(path: Path) -> bool:
    """A page under a `design/` folder."""
    return path.suffix.lower() in _HTML and any(part.lower() == DESIGN_DIR for part in path.parent.parts)
