"""Directories a content search does not go into."""

from __future__ import annotations

from pathlib import Path

# Per-OS application data folders. They are not build noise. On macOS 14 and later, merely
# descending into ~/Library/Application Support (other apps' containers) trips the App Data TCC
# protection, and macOS asks "would like to access data from other apps", a prompt the person never
# asked for and that is reachable whenever the workspace is a home folder. They are never walked. A
# workspace under one of them is still searched normally, because the guard matches directory names
# met during a walk.
OS_DATA_DIRS = {
    "Library",  # macOS
    "AppData",  # Windows
    "Application Data",  # Windows (legacy junction)
}

IGNORE_DIRS = {
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
} | OS_DATA_DIRS


def ignore_globs(base: Path) -> list[str]:
    """The exclusion globs for one search, minus any directory the search root itself lives in.

    The list is there to skip heavy generated descendants (`node_modules`, `venv`, `.git`), but it
    also holds home-folder names (`AppData`, `Library`, `Application Data`). `!**/AppData/**` then
    excludes the whole search when the workspace sits under one of them. On Windows, pytest's
    `tmp_path` is `…\\AppData\\Local\\Temp\\…`, every grep returned zero matches, and nothing looked
    wrong, because the ripgrep branch reported "no matches" and raised nothing.

    A folder the root lives under is by definition not a descendant worth skipping, so it is left out
    of the globs for this search. The Python fallback never had the bug, since it prunes children of
    the root and not the root's own path."""
    parts = {part.lower() for part in base.parts}
    return [f"!**/{name}/**" for name in sorted(IGNORE_DIRS) if name.lower() not in parts]
