"""DesignVersionsMixin: a session's design pages and their versions, as its checks recorded them.

Every write of a design page leaves the check's card on the write's message (`_design_check`): what
was found, what the write changed, what of that nobody pointed at, and a small picture. The list of a
page's versions is those cards, in the order of the thread. The list of the session's designs is the
pages under a `design/` folder in its folders, with the last card each one has. Nothing is stored for
either besides; a session that never designed has none, and a reopened session has what it had.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any, Iterator, Optional

from harness.tools.design.browser import render_page
from harness.tools.design.check import MAX_WIDTH, MIN_WIDTH
from harness.tools.design.locks import lock_state
from harness.tools.design.pages import DESIGN_DIR, SKIP_DIRS, is_design_file
from harness.tools.design.tokens import DESIGN_FILE, read_tokens

#: How many versions are handed back, newest kept.
_MAX_VERSIONS = 40
#: How many design pages the list hands back, newest kept.
_MAX_FILES = 200
#: How deep under a `design/` folder pages are looked for.
_MAX_DEPTH = 4
#: How many of DESIGN.md's colours the list names.
_MAX_COLORS = 8
_PAGE = (".html", ".htm")
#: `pricing.a.html` is variant `a` of `pricing.html` (phase 3c).
_VARIANT = re.compile(r"^(?P<stem>.+)\.(?P<letter>[a-z])\.(?P<ext>html?)$", re.IGNORECASE)


class DesignVersionsMixin:
    def design_versions(self, session_id: str, path: str) -> dict[str, Any]:
        """The versions of the design at `path`, oldest first."""
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        wanted = _key(target)
        versions: list[dict[str, Any]] = []
        for key, message, card in self._design_cards(session_id):
            if key != wanted:
                continue
            versions.append(
                {
                    "call_id": message.get("tool_call_id"),
                    "ts": message.get("ts"),
                    "summary": str(card.get("summary") or ""),
                    "issues": int(card.get("issues") or 0),
                    "changes": [str(line) for line in card.get("changes") or [] if isinstance(line, str)],
                    "unasked": [str(line) for line in card.get("unasked") or [] if isinstance(line, str)],
                    "thumb": card.get("thumb") if isinstance(card.get("thumb"), str) else None,
                }
            )
        for number, version in enumerate(versions, start=1):
            version["n"] = number
        return {"ok": True, "versions": versions[-_MAX_VERSIONS:]}

    def design_files(self, session_id: str) -> dict[str, Any]:
        """The session's design pages, newest first, each with what its last check said and showed.

        A page is one under a `design/` folder of the session's folders (the folder itself, or one a
        level down, as in a repo of several apps), not of one the session only reached into to read;
        a check of a page anywhere else (`check_design` on a report) does not make it a design. The project's DESIGN.md comes with its colours, for
        the card that stands for it.
        """
        if self._is_remote_record(self.session_store.load(session_id)):
            return {"ok": False, "error": "this session's files are on the remote host — not available here yet"}
        folders = self._session_folders(session_id)
        if not folders:
            return {"ok": False, "error": "no workspace"}
        primary = folders[0]
        # A folder the session only reached into to read a file is someone else's: its
        # `design/` pages are not this conversation's designs. A session that read one file in
        # Downloads listed Downloads' pricing pages and DESIGN.md as its own (owner, 2026-10-20).
        # A page there this session wrote still comes in below, by its check.
        own = [root for root in folders if root not in self._reached_folders(session_id)]
        rows: dict[str, dict[str, Any]] = {}
        for root in own:
            for page in _pages_under(root):
                rows.setdefault(_key(page), _row(page, root, primary))
        for key, message, card in self._design_cards(session_id):
            row = rows.get(key)
            if row is None:
                target, _ = self._artifact_target(session_id, str(card["path"]))
                if target is None:
                    continue
                holder = next((root for root in folders if target.is_relative_to(root)), primary)
                inside = target.relative_to(holder) if target.is_relative_to(holder) else target
                if not is_design_file(inside):
                    continue
                row = rows.setdefault(key, _row(target, holder, primary))
            row["checks"] += 1
            row["summary"] = str(card.get("summary") or "")
            row["issues"] = int(card.get("issues") or 0)
            if isinstance(card.get("thumb"), str):
                row["thumb"] = card["thumb"]
            if isinstance(message.get("ts"), (int, float)):
                row["checked_at"] = message["ts"]
        files = sorted(rows.values(), key=lambda row: row["mtime_ms"], reverse=True)[:_MAX_FILES]
        return {"ok": True, "files": files, "design_md": _design_md(own, primary)}

    def _reached_folders(self, session_id: str) -> set[Path]:
        """The extra roots the session was given because a call reached into them, not by a person."""
        record = self.session_store.load(session_id)
        reached: set[Path] = set()
        for extra in (record.extra_roots if record else []) or []:
            if isinstance(extra, dict) and extra.get("reached"):
                try:
                    reached.add(Path(str(extra.get("path", ""))).expanduser().resolve())
                except OSError:
                    continue
        return reached

    def design_shot(self, session_id: str, path: str, width: int, area: dict[str, Any]) -> dict[str, Any]:
        """A picture of one area of a page, laid out at `width`, from the harness's own browser: the
        part a person marked in the preview (Mark), for the message it goes into. The preview's own
        frame cannot be read by the app (another origin), so the page is drawn again here."""
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        try:
            width = int(width)
            clip = {key: int(area.get(key) or 0) for key in ("x", "y", "w", "h")}
        except (TypeError, ValueError):
            return {"ok": False, "error": "the width and the area are numbers"}
        if not MIN_WIDTH <= width <= MAX_WIDTH or clip["w"] <= 0 or clip["h"] <= 0:
            return {"ok": False, "error": "that width or area is out of bounds"}
        render = render_page(target, [width], "0", shots=False, clip=clip, timeout_s=30.0)
        if render.error or not render.region:
            return {"ok": False, "error": render.error or "the browser drew nothing"}
        return {"ok": True, "png": "data:image/png;base64," + base64.b64encode(render.region).decode("ascii")}

    def _design_cards(self, session_id: str) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
        """Each check card of the thread with the page it is about, in thread order."""
        resolved: dict[str, Optional[str]] = {}
        for message in self.session_messages(session_id):
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            cards = message.get("_design_check")
            if not isinstance(cards, list):
                continue
            for card in cards:
                if not isinstance(card, dict) or not isinstance(card.get("path"), str):
                    continue
                shown = card["path"]
                if shown not in resolved:
                    hit, _ = self._artifact_target(session_id, shown)
                    resolved[shown] = _key(hit) if hit is not None else None
                if resolved[shown] is not None:
                    yield resolved[shown], message, card


def _pages_under(root: Path) -> Iterator[Path]:
    """The pages under `root/design/` and `root/<folder>/design/`, a bounded walk."""
    candidates = [root / DESIGN_DIR]
    try:
        candidates += [
            child / DESIGN_DIR
            for child in sorted(root.iterdir())
            if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP_DIRS
        ]
    except OSError:
        pass
    found = 0
    for folder in candidates:
        if not folder.is_dir():
            continue
        base_depth = len(folder.parts)
        for current, dirs, names in os.walk(folder):
            if len(Path(current).parts) - base_depth >= _MAX_DEPTH:
                dirs[:] = []
            else:
                dirs[:] = [name for name in dirs if not name.startswith(".") and name not in SKIP_DIRS]
            for name in sorted(names):
                if Path(name).suffix.lower() in _PAGE:
                    found += 1
                    yield Path(current) / name
                    if found >= _MAX_FILES:
                        return


def _row(page: Path, root: Path, primary: Path) -> dict[str, Any]:
    """One design page as the list names it: inside the workspace by its path there, elsewhere by
    its full path, so the name opens the same file whichever folder holds it."""
    shown = page.relative_to(root).as_posix() if root == primary and page.is_relative_to(root) else str(page)
    try:
        mtime_ms = int(page.stat().st_mtime * 1000)
    except OSError:
        mtime_ms = 0
    row: dict[str, Any] = {
        "path": shown,
        "name": page.name,
        "mtime_ms": mtime_ms,
        "checks": 0,
        "summary": "",
        "issues": 0,
        "thumb": None,
        "checked_at": None,
        "lock": lock_state(page),
    }
    variant = _VARIANT.match(page.name)
    if variant:
        row["variant_of"] = shown[: len(shown) - len(page.name)] + f"{variant['stem']}.{variant['ext']}"
        row["letter"] = variant["letter"].lower()
    return row


def _design_md(folders: list[Path], primary: Path) -> Optional[dict[str, Any]]:
    """The DESIGN.md at the top of the session's folders, workspace first, with its colours."""
    for root in folders:
        found = root / DESIGN_FILE
        if not found.is_file():
            continue
        colors: list[str] = []
        for rgb in read_tokens(found).colors:
            spelled = "#{:02x}{:02x}{:02x}".format(*rgb)
            if spelled not in colors:
                colors.append(spelled)
            if len(colors) >= _MAX_COLORS:
                break
        shown = DESIGN_FILE if root == primary else str(found)
        return {"path": shown, "colors": colors}
    return None


def _key(path: Path) -> str:
    """One spelling of a file, as the OS compares them (case-folded on Windows)."""
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))
