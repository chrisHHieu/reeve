"""ArtifactsMixin: listing, previewing and revealing the files a session produced."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import spawn
from harness.tools.search.ignore import OS_DATA_DIRS

from .file_text import OFFERED, read_text

_MAX_LISTED = 80
_MAX_FOLDER_ENTRIES = 500
_MAX_TEXT_CHARS = 500_000
_MAX_BINARY_PREVIEW = 25 * 1024 * 1024  # a base64-over-JSON preview gets heavy past this

_KINDS: dict[str, tuple[str, ...]] = {
    "markdown": (".md", ".markdown"),
    "html": (".html", ".htm"),
    # What a browser draws by itself. TIFF and HEIC are not on the list: no webview here
    # decodes them, and a broken picture is worse than saying so.
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".avif"),
    "pdf": (".pdf",),
    "sheet": (".xlsx", ".xls", ".xlsm", ".ods"),
    "office": (".pptx", ".ppt", ".pptm", ".docx", ".doc", ".docm"),
    "csv": (".csv", ".tsv"),
    # Played by the webview itself, from the preview door: never read here.
    "video": (".mp4", ".m4v", ".webm", ".mov", ".ogv"),
    "audio": (".mp3", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac"),
    "code": (".py", ".js", ".ts", ".tsx", ".css", ".json"),
}
_KIND_OF_SUFFIX = {suffix: kind for kind, suffixes in _KINDS.items() for suffix in suffixes}
# A file is listed as an artifact when it has a kind, or is plain text of a few kinds.
_LISTED_SUFFIXES = set(_KIND_OF_SUFFIX) | {".txt"}
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
}
_SKIPPED_DIRS = {"node_modules", "target", "dist", "__pycache__"} | OS_DATA_DIRS


def artifact_kind(path: Path) -> str:
    return _KIND_OF_SUFFIX.get(path.suffix.lower(), "text")


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def _too_large(size: int) -> str:
    """Says the two numbers, so the person knows why and by how much."""
    return f"This file is {_megabytes(size)}. Files up to {_megabytes(_MAX_BINARY_PREVIEW)} are shown here."


def _folder_listing(target: Path, path: str) -> dict[str, Any]:
    """A folder as a listing the viewer can draw. A model sometimes links a whole package, a
    skill's build folder say, and a dead end would be worse than a listing."""
    try:
        children = sorted(target.iterdir(), key=lambda child: (child.is_file(), child.name.lower()))
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    entries: list[dict[str, Any]] = []
    for child in children[:_MAX_FOLDER_ENTRIES]:
        try:
            size = 0 if child.is_dir() else child.stat().st_size
        except OSError:
            continue
        entries.append({"name": child.name, "dir": child.is_dir(), "size": size})
    return {"ok": True, "path": path, "kind": "folder", "entries": entries}


class ArtifactsMixin:
    def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        root = self._artifact_scan_root(session_id)
        if root is None or not root.is_dir():
            return []
        found: list[dict[str, Any]] = []
        # `os.walk` with the pruning in place, and not `rglob`: rglob descends first and filters
        # after, so a home-directory workspace walked into ~/Library and raised the macOS "access
        # data from other apps" prompt on every turn. Pruned folders are never entered at all.
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIPPED_DIRS]
            for name in files:
                path = Path(dirpath) / name
                if name.startswith(".") or path.suffix.lower() not in _LISTED_SUFFIXES:
                    continue
                try:
                    stat = path.stat()
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                found.append(
                    {
                        "path": str(path.relative_to(root)),
                        # The absolute path is for "Copy path", since the relative one is
                        # useless outside the app.
                        "abs_path": str(path),
                        "name": path.name,
                        "kind": artifact_kind(path),
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                    }
                )
        found.sort(key=lambda artifact: artifact["modified_at"], reverse=True)
        return found[:_MAX_LISTED]

    def _artifact_target(
        self, session_id: str, path: str, *, allow_dir: bool = False
    ) -> tuple[Optional[Path], Optional[str]]:
        """Resolve an artifact path under one of the session's folders, workspace first."""
        if self._is_remote_record(self.session_store.load(session_id)):
            # Reads and reveals resolve on this machine's disk, and a remote session's paths are
            # POSIX names on the other host. Refusing beats resolving a local spelling of them.
            return None, "this session's files are on the remote host — not available here yet"
        folders = self._session_folders(session_id)
        if not folders:
            return None, "no workspace"
        missing = False
        for root in folders:
            target = (root / path).expanduser().resolve()
            if not target.is_relative_to(root):
                continue
            if (allow_dir and target.is_dir()) or target.is_file():
                return target, None
            missing = True
        if missing:
            return None, "This isn't in the conversation's folder anymore — it may have been moved or deleted."
        return None, "path escapes workspace"

    def read_artifact(self, session_id: str, path: str, encoding: Optional[str] = None) -> dict[str, Any]:
        target, error = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": error}
        if target.is_dir():
            return _folder_listing(target, path)
        kind = artifact_kind(target)
        if kind in ("office", "video", "audio"):
            # Not read here: a video is played, and a Word or PowerPoint file drawn, by the
            # app's window from the file's own bytes (the preview door), so only what the file
            # is and how big it is are said.
            try:
                size = target.stat().st_size
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "path": path, "kind": kind, "size": size}
        if kind in ("image", "pdf", "sheet"):
            size = target.stat().st_size
            if size > _MAX_BINARY_PREVIEW:
                return {"ok": False, "error": _too_large(size)}
            mime = _MIME.get(target.suffix.lower(), "application/octet-stream")
            data = base64.b64encode(target.read_bytes()).decode("ascii")
            return {"ok": True, "path": path, "kind": kind, "data_url": f"data:{mime};base64,{data}"}
        try:
            stat = target.stat()
            if stat.st_size > _MAX_BINARY_PREVIEW:
                return {"ok": False, "error": _too_large(stat.st_size)}
            raw = target.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        # Any encoding, not only UTF-8 (`file_text.py`): a Vietnamese Windows-1258 note or a
        # UTF-16 log was "binary" to the person while `read_file` read it for the model.
        decoded = read_text(raw, encoding)
        if decoded is None:
            suffix = target.suffix.lower() or "this kind of"
            return {"ok": False, "error": f"This is not a text file, and there is no viewer here for {suffix} files."}
        text, encoding = decoded
        return {
            "ok": True,
            "path": path,
            "kind": kind,
            "content": text[:_MAX_TEXT_CHARS],
            "truncated": len(text) > _MAX_TEXT_CHARS,
            # What the bytes were read as; the save writes the same back.
            "encoding": encoding,
            "encodings": list(OFFERED),
            # The editor's save token: what the file looked like when it was read.
            "size": stat.st_size,
            "mtime_ms": int(stat.st_mtime * 1000),
        }

    def reveal_artifact(self, session_id: str, path: str, mode: str = "reveal") -> dict[str, Any]:
        """Show the file in the OS file manager (`reveal`) or open it with its default app
        (`open`). The server runs on the person's machine in both the desktop and the browser
        builds, so this is local. A folder opens as itself whatever the mode."""
        target, error = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": error}
        reveal = mode == "reveal" and not target.is_dir()
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        try:
            if sys.platform == "darwin":
                spawn(["open", "-R", str(target)] if reveal else ["open", str(target)], **quiet)
            elif sys.platform == "win32":
                if reveal:
                    # Explorer wants the path glued to the switch: /select,<path>
                    spawn(["explorer", f"/select,{target}"])
                else:
                    os.startfile(str(target))  # type: ignore[attr-defined]  # the default app
            else:
                spawn(["xdg-open", str(target.parent if reveal else target)], **quiet)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
