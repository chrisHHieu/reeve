"""PreviewGrantsMixin: letting the preview door serve a file of this session."""

from __future__ import annotations

from typing import Any

from ..preview_door import SERVED, PreviewDoor


class PreviewGrantsMixin:
    @property
    def preview_door(self) -> PreviewDoor:
        door = getattr(self, "_preview_door", None)
        if door is None:
            door = self._preview_door = PreviewDoor()
        return door

    def preview_grant(
        self, session_id: str, path: str, *, internet: bool = False, single: bool = False
    ) -> dict[str, Any]:
        """An address for one of the session's files at the preview door.

        A page is granted with its session folder, so its own files and the pages it links to
        answer; `single` grants that one file alone (a media element, a document's bytes).
        `internet` is the person's answer to "let this page use the internet?": the caller is
        the app, behind the sidecar token, and never a page.
        """
        target, error = self._artifact_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if target.suffix.lower() not in SERVED:
            return {"ok": False, "error": f"{target.suffix.lower() or 'this kind of'} files are not shown this way"}
        root = next((folder for folder in self._session_folders(session_id) if target.is_relative_to(folder)), None)
        if root is None:
            return {"ok": False, "error": "path escapes workspace"}
        rel = target.relative_to(root).as_posix()
        door = self.preview_door
        key = door.grant(root, internet=bool(internet), only=rel if single else None)
        return {
            "ok": True,
            "url": door.url(key, rel),
            "origin": door.origin,
            "key": key,
            "internet": bool(internet),
        }

    def preview_revoke(self, key: str) -> dict[str, Any]:
        self.preview_door.revoke(str(key or ""))
        return {"ok": True}
