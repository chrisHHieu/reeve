"""FileEntriesMixin: the file manager's writes, which are save, create, rename, move and delete
(to the system's trash, `trash.py`).

They are confined to a session's WRITABLE folders (a read-only grant stays read-only), a
relative path that escapes its folder is refused, and credentials and the git store are never
touched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from .file_text import write_text

_DENIED = re.compile(
    r"(^|/)(\.env[^/]*|\.ssh|\.aws|\.gnupg|\.git)(/|$)|\.(pem|key|p12|pfx)$",
    re.IGNORECASE,
)
_NAME = re.compile(r"^[^/\\\x00]{1,120}$")
_MAX_SAVE_BYTES = 2 * 1024 * 1024
_EXISTS = "an entry with that name already exists"


def _tidy(path: str) -> str:
    return str(path).replace("\\", "/").strip("/")


def _relative_path(path: str) -> tuple[Optional[str], Optional[str]]:
    """(clean relative path, error). A path must be relative to a session folder and stay in
    it; `.` segments are dropped."""
    rel = str(path or "").replace("\\", "/").strip()
    if not rel:
        return None, "no path named"
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None, "path must be relative to a session folder"
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if ".." in parts:
        return None, "path escapes the session folder"
    rel = "/".join(parts)
    if _DENIED.search(rel):
        return None, "that path is protected and cannot be changed here"
    return rel, None


class FileEntriesMixin:
    def _file_edit_target(
        self, session_id: str, path: str, *, missing_ok: bool = False
    ) -> tuple[Optional[Path], Optional[Path], Optional[str]]:
        """(target, root, error) for a mutation. With `missing_ok`, a path that does not exist
        yet also resolves, provided its parent does and stays inside the root."""
        record = self.session_store.load(session_id)
        if record is None:
            # Reads fall back to the default workspace for legacy ids, but a WRITE needs a
            # session to belong to, so an unknown id is refused and not guessed at.
            return None, None, "no such session"
        if self._is_remote_record(record):
            return None, None, "this session's files are on the remote host — not available here yet"
        roots = self._session_folders(session_id, writable=True)
        if not roots:
            return None, None, "this session has no writable folder"
        candidates = list(roots)
        raw = str(path or "").strip()
        if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
            # The file manager's tree names an entry by its full path (a root's path plus the
            # names below it). Such a path is taken only when it lies inside one of the
            # session's writable folders, and is then judged as that folder's relative path,
            # so every rule below still applies. Found 2026-10-11: Rename and Move from the
            # tree were refused with "path must be relative" on every real session.
            absolute = Path(raw).resolve()
            owner = next((root for root in roots if absolute.is_relative_to(root)), None)
            if owner is None:
                return None, None, "that path is not in the session's writable folders"
            raw = absolute.relative_to(owner).as_posix()
            candidates = [owner]
        rel, error = _relative_path(raw)
        if rel is None:
            return None, None, error
        for root in candidates:
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                continue
            if target.exists() or (missing_ok and target.parent.is_dir()):
                return target, root, None
        if missing_ok:
            return None, None, "the destination folder does not exist"
        return None, None, "that path is not in the session's writable folders"

    def write_file_entry(
        self, session_id: str, path: str, content: Any, mtime_ms: Any = None, encoding: Optional[str] = None
    ) -> dict[str, Any]:
        """Save an edited text file (the manager's Edit mode).

        The write is atomic (a temp file, then a replace) and guarded like every other mutation.
        `mtime_ms` is the token `read_artifact` returned: when the file changed on disk since,
        the save is refused with `conflict`, so the surface can ask before it overwrites.
        """
        target, _root, error = self._file_edit_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if target.is_dir():
            return {"ok": False, "error": "that path is a folder"}
        if not isinstance(content, str):
            return {"ok": False, "error": "no content"}
        try:
            before = target.stat()
            original = target.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            data = write_text(content, original, encoding)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if len(data) > _MAX_SAVE_BYTES:
            return {"ok": False, "error": "this file is too large to save here"}
        if mtime_ms is not None:
            try:
                expected = int(mtime_ms)
            except (TypeError, ValueError):
                expected = None
            if expected is not None and abs(int(before.st_mtime * 1000) - expected) > 1:
                return {
                    "ok": False,
                    "conflict": True,
                    "mtime_ms": int(before.st_mtime * 1000),
                    "error": "file changed on disk",
                }
        scratch = target.with_name(f".{target.name}.reeve-save")
        try:
            scratch.write_bytes(data)
            os.replace(scratch, target)
        except OSError as exc:
            try:
                scratch.unlink()
            except OSError:
                pass
            return {"ok": False, "error": str(exc)}
        after = target.stat()
        return {"ok": True, "path": _tidy(path), "size": after.st_size, "mtime_ms": int(after.st_mtime * 1000)}

    def create_file_entry(self, session_id: str, path: str, kind: str = "file") -> dict[str, Any]:
        target, _root, error = self._file_edit_target(session_id, path, missing_ok=True)
        if target is None:
            return {"ok": False, "error": error}
        if target.exists():
            return {"ok": False, "error": _EXISTS}
        try:
            if kind == "folder":
                target.mkdir()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8"):
                    pass
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": _tidy(path)}

    def _rename_within(self, target: Path, new_target: Path, root: Path) -> dict[str, Any]:
        try:
            target.rename(new_target)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(new_target.relative_to(root)).replace("\\", "/")}

    def rename_file_entry(self, session_id: str, path: str, name: str) -> dict[str, Any]:
        target, root, error = self._file_edit_target(session_id, path)
        if target is None or root is None:
            return {"ok": False, "error": error}
        if target == root:
            return {"ok": False, "error": "a session folder itself cannot be changed here"}
        new_name = str(name or "").strip()
        if not _NAME.match(new_name) or new_name in (".", ".."):
            return {"ok": False, "error": "that name is not allowed"}
        if _DENIED.search(new_name):
            return {"ok": False, "error": "that name is protected and cannot be used here"}
        new_target = target.parent / new_name
        if new_target.exists():
            return {"ok": False, "error": _EXISTS}
        return self._rename_within(target, new_target, root)

    def delete_file_entry(self, session_id: str, path: str) -> dict[str, Any]:
        """Move a file or folder to the system's trash (the file manager's Delete, after it asked).

        Guarded like the other mutations, and never a session folder itself: deleting a root
        would take the session's whole workspace with it.
        """
        from . import trash

        target, root, error = self._file_edit_target(session_id, path)
        if target is None or root is None:
            return {"ok": False, "error": error}
        if target == root:
            return {"ok": False, "error": "a session folder itself cannot be deleted here"}
        refused = trash.move_to_trash(target)
        if refused:
            return {"ok": False, "error": refused}
        return {"ok": True, "path": _tidy(path)}

    def move_file_entry(self, session_id: str, path: str, to: str) -> dict[str, Any]:
        target, root, error = self._file_edit_target(session_id, path)
        if target is None or root is None:
            return {"ok": False, "error": error}
        if target == root:
            return {"ok": False, "error": "a session folder itself cannot be changed here"}
        destination, destination_root, destination_error = self._file_edit_target(session_id, to or ".")
        if destination is None or destination_root is None:
            return {"ok": False, "error": destination_error or "the destination folder does not exist"}
        if not destination.is_dir():
            return {"ok": False, "error": "the destination is not a folder"}
        if destination == target.parent:
            return {"ok": True, "path": str(target.relative_to(root)).replace("\\", "/")}
        if destination_root != root:
            return {"ok": False, "error": "cannot move between different session folders"}
        new_target = destination / target.name
        if new_target.exists():
            return {"ok": False, "error": f"{_EXISTS} there"}
        return self._rename_within(target, new_target, root)
