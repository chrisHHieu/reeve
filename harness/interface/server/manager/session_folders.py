"""SessionFoldersMixin: which folders of this machine a session's file features may touch."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_NOT_ID = {".", ".."}


class SessionFoldersMixin:
    def _is_remote_record(self, record) -> bool:
        """Whether the session runs on another host. Its files live there, so anything that
        reads or writes this machine's disk on its behalf has to refuse."""
        return record is not None and bool(getattr(record, "environment_id", None))

    def _session_folders(self, session_id: str, *, writable: bool = False) -> list[Path]:
        """The folders a session's files may be browsed or changed in: its workspace, then its
        scratch folder, then the extra roots it was granted. With `writable`, only those it may
        write to, so a read-only grant stays read-only.

        Resolution order matters. Gated sessions keep their scratch BESIDE the workspace, and a
        single root would orphan every transcript link that points at scratch.
        """
        record = self.session_store.load(session_id)
        if self._is_remote_record(record):
            return []
        workspace = record.workspace if record else self.default_workspace
        folders: list[Path] = []
        if workspace:
            root = Path(workspace).expanduser().resolve()
            if not writable or root.is_dir():
                folders.append(root)
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in _NOT_ID:
            scratch = (self.scratch_base() / session_id).resolve()
            if scratch.is_dir() and scratch not in folders:
                folders.append(scratch)
        for extra in (record.extra_roots if record else []) or []:
            if writable and not extra.get("writable"):
                continue
            path = Path(str(extra.get("path", ""))).expanduser()
            if path.is_dir() and path.resolve() not in folders:
                folders.append(path.resolve())
        return folders

    def _artifact_scan_root(self, session_id: str) -> Optional[Path]:
        """The folder the Artifacts panel lists: the session's SCRATCH surface only. For an
        orphan session that is the workspace itself, and for a folder-gated one it is the side
        scratch root, never the person's repository, which would list a whole codebase as
        artifacts."""
        record = self.session_store.load(session_id)
        if self._is_remote_record(record):
            # A remote session's files are on the other host, and listing this machine's scratch
            # for it would draw a local folder as the remote session's artifacts.
            return None
        workspace = record.workspace if record else self.default_workspace
        if workspace and self.is_temp_workspace(workspace):
            return Path(workspace).expanduser().resolve()
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in _NOT_ID:
            scratch = (self.scratch_base() / session_id).resolve()
            if scratch.is_dir():
                return scratch
        # Sessions from before universal scratch, on a custom scratch base: a workspace that is
        # itself disposable still scans.
        if workspace and not record:
            return Path(workspace).expanduser().resolve()
        return None
