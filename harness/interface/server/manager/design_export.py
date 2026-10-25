"""DesignExportMixin: a design page taken out of Reeve, and the Browser tab's page read by Reeve.

The design tab's row carries what OpenDesign's file viewer carries (the owner's pick, 2026-10-19):
Export (a PDF, a picture of the whole page, a zip, one HTML file), Share (the same as a picture or
text on the clipboard), and open in an editor. A PDF and a picture are drawn by the harness's own
browser at the width the person is looking at, with the internet only if they let the page out; the
zip and the one file are `tools/design/export.py`. A saved file goes to Downloads, and only a file
saved here can be shown in its folder afterwards: the surface cannot name any other path.

An editor is opened through its own address (`vscode://file/…`), handed to the system the way a
link is, so no command line is built from a file's name; an editor is offered only when the system
knows its address (Windows) or its command is on the PATH (elsewhere).

The Browser tab asks the same browser for its page: a picture of the first screen, the brief of its
look (`tools/design/brief.py`), or its HTML saved. That load is the harness's, signed out of
everything, with the network on, because the page is on the web.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from harness.platform.spawn import spawn
from harness.tools.design.brief import BRIEF_JS, brief_text
from harness.tools.design.browser import render_page, render_url
from harness.tools.design.check import MAX_WIDTH, MIN_WIDTH
from harness.tools.design.export import save_download, standalone_html, zip_bytes

#: The editors a design can be opened in: id, name, the address scheme it registers, its command.
EDITORS = (
    ("vscode", "VS Code", "vscode", "code"),
    ("cursor", "Cursor", "cursor", "cursor"),
    ("windsurf", "Windsurf", "windsurf", "windsurf"),
)
_KINDS = ("pdf", "png", "zip", "html")
_SUFFIX = {"pdf": ".pdf", "png": ".png", "zip": ".zip", "html": ".standalone.html"}


def _editor_installed(scheme: str, command: str) -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme) as key:
                winreg.QueryValueEx(key, "URL Protocol")
            return True
        except OSError:
            return False
    return shutil.which(command) is not None


def _open_address(address: str) -> None:
    """Hand an address to the system, as a clicked link is."""
    if sys.platform == "win32":
        os.startfile(address)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        spawn(["open", address], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        spawn(["xdg-open", address], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _width(value: Any) -> Optional[int]:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return None
    return width if MIN_WIDTH <= width <= MAX_WIDTH else None


def _png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class DesignExportMixin:
    def _design_root(self, session_id: str, target: Path) -> Path:
        for root in self._session_folders(session_id):
            if target.is_relative_to(root):
                return root
        return target.parent

    def _remember_download(self, path: Path) -> None:
        saved = self.__dict__.setdefault("_design_downloads", set())
        saved.add(str(path.resolve()))

    def design_export(
        self, session_id: str, path: str, kind: str, width: Any = 1440, internet: bool = False, deliver: str = "file"
    ) -> dict[str, Any]:
        """`path` as `kind` (pdf, png, zip, html). `deliver` "file" saves it to Downloads; "data"
        hands back a picture (png) or the text (html) for the clipboard."""
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if kind not in _KINDS:
            return {"ok": False, "error": f"no export as {kind!r}"}
        root = self._design_root(session_id, target)
        if kind in ("pdf", "png"):
            chosen = _width(width)
            if chosen is None:
                return {"ok": False, "error": "that width is out of bounds"}
            render = render_page(
                target, [chosen], "0", shots=False, capture="pdf" if kind == "pdf" else "page", network=internet, timeout_s=45.0
            )
            if render.error or not render.capture:
                return {"ok": False, "error": render.error or "the browser drew nothing"}
            data = render.capture
        elif kind == "zip":
            data = zip_bytes(target, root, self._session_folders(session_id))
        else:
            data = standalone_html(target, root).encode("utf-8")
        if deliver == "data":
            if kind == "png":
                return {"ok": True, "png": _png(data)}
            if kind == "html":
                return {"ok": True, "html": data.decode("utf-8")}
            return {"ok": False, "error": "only a picture or the HTML can be copied"}
        name = target.name[: -len(target.suffix)] if target.suffix else target.name
        try:
            saved = save_download(name + _SUFFIX[kind], data)
        except OSError as exc:
            return {"ok": False, "error": f"could not save it: {exc}"}
        self._remember_download(saved)
        return {"ok": True, "saved": str(saved), "name": saved.name, "folder": saved.parent.name}

    def reveal_download(self, path: str) -> dict[str, Any]:
        """Show a file this process saved to Downloads in its folder; nothing else."""
        try:
            absolute = str(Path(path).resolve())
        except (OSError, ValueError):
            return {"ok": False, "error": "invalid path"}
        if absolute not in self.__dict__.get("_design_downloads", set()) or not Path(absolute).is_file():
            return {"ok": False, "error": "not a file Reeve saved"}
        command = self._reveal_command(absolute)
        if command is None:
            return {"ok": False, "error": "no file manager on this system"}
        try:
            spawn(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return {"ok": False, "error": f"could not open it: {exc}"}
        return {"ok": True}

    def design_editors(self) -> dict[str, Any]:
        """The editors on this machine a file can be opened in."""
        return {
            "ok": True,
            "editors": [{"id": ident, "name": name} for ident, name, scheme, command in EDITORS if _editor_installed(scheme, command)],
        }

    def open_in_editor(self, session_id: str, path: str, editor: str) -> dict[str, Any]:
        """Open one of the session's files in an editor, through the editor's own address."""
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        found = next((row for row in EDITORS if row[0] == editor), None)
        if found is None or not _editor_installed(found[2], found[3]):
            return {"ok": False, "error": "that editor is not on this machine"}
        posix = target.as_posix()
        address = f"{found[2]}://file{'' if posix.startswith('/') else '/'}{quote(posix, safe='/:')}"
        try:
            _open_address(address)
        except OSError as exc:
            return {"ok": False, "error": f"could not open it: {exc}"}
        return {"ok": True}

    def browser_capture(self, url: str, kind: str, width: Any = 1280) -> dict[str, Any]:
        """The Browser tab's page as Reeve's browser loads it: "view" (a picture of the first
        screen), "brief" (its look in words), or "html" (its HTML, saved to Downloads)."""
        chosen = _width(width) or 1280
        if kind == "view":
            render = render_url(url, chosen, capture="view")
            if render.error or not render.capture:
                return {"ok": False, "error": render.error or "the browser drew nothing"}
            return {"ok": True, "png": _png(render.capture)}
        if kind == "brief":
            render = render_url(url, chosen, BRIEF_JS)
            measured = render.widths[0].measured if render.widths else {}
            if render.error or not measured:
                return {"ok": False, "error": render.error or "the page could not be read"}
            host = urlsplit(url).netloc or url
            return {"ok": True, "brief": brief_text(measured, host), "title": str(measured.get("title") or "")}
        if kind == "html":
            render = render_url(url, chosen, capture="html")
            if render.error or not render.capture:
                return {"ok": False, "error": render.error or "the page could not be read"}
            host = (urlsplit(url).netloc or "page").replace(":", "-")
            try:
                saved = save_download(f"{host}.html", render.capture)
            except OSError as exc:
                return {"ok": False, "error": f"could not save it: {exc}"}
            self._remember_download(saved)
            return {"ok": True, "saved": str(saved), "name": saved.name, "folder": saved.parent.name}
        return {"ok": False, "error": f"no capture {kind!r}"}
