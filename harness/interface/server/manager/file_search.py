"""FileSearchMixin: the file picker behind `/open`, and the list the composer's "@" filters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from harness.tools.search.ignore import IGNORE_DIRS as _IGNORE_DIRS

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
# The list is a menu and not a filesystem dump, and the walk is bounded so that a workspace set
# to a home directory cannot stall the request.
_SCAN_CEILING = 5000


def _rank(entry: dict[str, Any], query: str) -> tuple[int, str]:
    """Names that start with the query first, then names that contain it, then paths that do."""
    path = entry["path"].lower()
    if not query:
        return (0, path)
    name = entry["name"].lower()
    if name.startswith(query):
        return (0, path)
    return (1 if query in name else 2, path)


_REMOTE_REFUSAL = {
    "ok": False,
    "error": "This session's files live on the remote host; browsing them here is not available yet.",
    "files": [],
    "truncated": False,
}


class FileSearchMixin:
    def _walk_session_files(self, session_id: str):
        """Every file of the session's own folders, as `(root, relative, resolved, name)`, and a
        last `None` when the walk stopped at the scan ceiling. Pruned the way `grep` prunes; a
        symlink out of its root is refused, as a read of it would be."""
        scanned = 0
        for root in self._session_folders(session_id):
            if not root.is_dir():
                continue
            for dirpath, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _IGNORE_DIRS]
                for name in files:
                    if name.startswith("."):
                        continue
                    scanned += 1
                    if scanned > _SCAN_CEILING:
                        yield None
                        return
                    try:
                        path = Path(dirpath) / name
                        if not path.is_file():
                            continue
                        resolved = path.resolve()
                        relative = str(resolved.relative_to(root)).replace(os.sep, "/")
                    except (OSError, ValueError):
                        continue
                    yield root, relative, resolved, name

    def file_index(self, session_id: str) -> dict[str, Any]:
        """The session's files as relative paths, for the composer's "@" to filter as it is
        typed. The picker used to ask for a search per keystroke, and each one walked the
        whole tree again (owner, 2026-10-13: "@" was not live). Bounded by the same ceiling;
        `truncated` says it was reached."""
        if self._is_remote_record(self.session_store.load(session_id)):
            return dict(_REMOTE_REFUSAL)
        paths: list[str] = []
        truncated = False
        for entry in self._walk_session_files(session_id):
            if entry is None:
                truncated = True
                break
            paths.append(entry[1])
        paths.sort(key=str.lower)
        return {"ok": True, "files": paths, "truncated": truncated}

    def search_files(self, session_id: str, query: str = "", limit: int = 50) -> dict[str, Any]:
        """Search the session's folders for the picker.

        The walk covers the session's own roots and never a path the caller supplies, so a
        traversal is not a reachable shape. It prunes the folders `grep` prunes (`.git`,
        `node_modules`, `venv` and the rest: one vocabulary for "not a file a person means",
        shared so the picker and the search tool agree) and refuses a symlink whose target
        leaves its root. Each hit is `{path (relative to its root), abs_path, name}`, and
        `truncated` says the scan or the cap cut the list.
        """
        if self._is_remote_record(self.session_store.load(session_id)):
            # The walk is a walk of this machine's disk, and a bound session's files are on the
            # other host. An empty list would read as "no files", which would be a lie, so it
            # refuses with the reason and the surface can say remote browsing is not here yet.
            return dict(_REMOTE_REFUSAL)
        needle = (query or "").strip().lower()
        try:
            cap = max(1, min(int(limit), _MAX_LIMIT))
        except (TypeError, ValueError):
            cap = _DEFAULT_LIMIT
        hits: list[dict[str, Any]] = []
        truncated = False
        for entry in self._walk_session_files(session_id):
            if entry is None:
                truncated = True
                break
            _root, relative, resolved, name = entry
            if needle and needle not in relative.lower():
                continue
            hits.append({"path": relative, "abs_path": str(resolved), "name": name})
        hits.sort(key=lambda entry: _rank(entry, needle))
        if len(hits) > cap:
            truncated = True
            hits = hits[:cap]
        return {"ok": True, "files": hits, "truncated": truncated}
