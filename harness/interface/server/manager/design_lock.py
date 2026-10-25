"""DesignLockMixin: the person locks a design page, and the harness measures it into a handoff.

Locking is two calls from the lock dialog: `design_lock_preview` opens the page in the harness's
browser and says what was measured and what the check still finds, writing nothing; `design_lock`
writes the lock and the handoff (`tools/design/handoff.py`). The second reuses the first's
measurement while the page is the same file, by its sha256, so the person does not wait twice;
a page that changed in between is measured again, because the lock names the version it measured.

The files are the harness's, written at the person's word: no model writes or edits them, and the
request to build from the handoff is put in the composer for the person to send.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from harness.tools.design.handoff import Locked, lock_summary, measure_for_lock, write_lock
from harness.tools.design.locks import lock_state, page_sha
from harness.tools.design.pages import is_design_file

#: How many measured pages are kept for the lock that follows a preview.
_KEEP = 8


class DesignLockMixin:
    def _lock_target(self, session_id: str, path: str) -> tuple[Optional[Path], str, str]:
        """The page, as shown, or why it cannot be locked."""
        if self._is_remote_record(self.session_store.load(session_id)):
            return None, "", "this session's files are on the remote host — not available here yet"
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return None, "", error
        folders = self._session_folders(session_id)
        holder = next((root for root in folders if target.is_relative_to(root)), None)
        inside = target.relative_to(holder) if holder is not None else target
        if not is_design_file(inside):
            return None, "", "only a page under a design/ folder can be locked"
        shown = inside.as_posix() if holder is not None and folders and holder == folders[0] else str(target)
        return target, shown, ""

    def _build_target(self, session_id: str) -> Optional[dict[str, Any]]:
        """The project the design would be built into: the session's workspace, unless it is the
        scratch folder a session without a project gets."""
        folders = self._session_folders(session_id)
        if not folders:
            return None
        workspace = folders[0]
        if self.is_temp_workspace(str(workspace)):
            return None
        return {"name": workspace.name, "path": str(workspace)}

    def _measured(self, session_id: str, target: Path, shown: str) -> Locked:
        """The page measured for its lock, kept by its sha256 for the lock that follows."""
        kept: dict[tuple[str, str], Locked] = self.__dict__.setdefault("_design_lock_measures", {})
        sha = page_sha(target)
        key = (session_id, str(target))
        hit = kept.get(key)
        if hit is not None and hit.sha256 == sha and not hit.error:
            return hit
        locked = measure_for_lock(target, shown=shown, roots=self._session_folders(session_id))
        kept[key] = locked
        while len(kept) > _KEEP:
            kept.pop(next(iter(kept)))
        return locked

    def design_lock_preview(self, session_id: str, path: str) -> dict[str, Any]:
        """What locking `path` would record, measured now; nothing is written."""
        target, shown, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        locked = self._measured(session_id, target, shown)
        if locked.error:
            return {"ok": False, "error": f"the page could not be opened: {locked.error}"}
        return {
            "ok": True,
            "summary": lock_summary(locked),
            "state": lock_state(target),
            "target": self._build_target(session_id),
        }

    def design_lock(self, session_id: str, path: str) -> dict[str, Any]:
        """Lock `path`: write its lock and its handoff, from the measurement of this version."""
        target, shown, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        locked = self._measured(session_id, target, shown)
        if locked.error:
            return {"ok": False, "error": f"the page could not be opened: {locked.error}"}
        try:
            record = write_lock(locked)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"the lock could not be written: {exc}"}
        return {
            "ok": True,
            "state": lock_state(target),
            "handoff": record["handoff"],
            "lock_file": record["lock_file"],
            "target": self._build_target(session_id),
        }

    def design_lock_state(self, session_id: str, path: str) -> dict[str, Any]:
        """Whether `path` is locked, and whether it changed since."""
        target, _, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        return {"ok": True, "state": lock_state(target), "target": self._build_target(session_id)}
