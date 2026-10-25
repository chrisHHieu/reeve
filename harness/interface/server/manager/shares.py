"""ShareMixin — the `/share` state over a person-configured endpoint.

No hosted backend exists here, and none is faked: `share_status` reports whether an
endpoint is configured, `share_session` uploads the exported transcript through
`harness/session/share.py`, and `unshare_session` revokes only when the configuration and the
stored record both make that possible. Records live in a tiny JSON file beside the
other per-install stores — a share is a fact about a session that must survive a
restart (otherwise `/unshare` could never find what to revoke).
"""

from __future__ import annotations

from harness.config.config import load_config

import json
import threading
from pathlib import Path
from typing import Any, Optional

from harness.session.share import revoke_session, share_settings, upload_session


class ShareStore:
    """`{session_id: {url, share_id, shared_at, endpoint}}`, persisted as JSON."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._records: dict[str, dict[str, Any]] = (
                {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
                if isinstance(raw, dict)
                else {}
            )
        except (OSError, ValueError):
            self._records = {}

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._records, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            pass  # best-effort: a share that worked must not fail on a disk hiccup

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self._records.get(session_id)
            return dict(record) if record else None

    def set(self, session_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._records[session_id] = dict(record)
            self._save()

    def clear(self, session_id: str) -> None:
        with self._lock:
            if self._records.pop(session_id, None) is not None:
                self._save()


class ShareMixin:
    def share_settings(self) -> dict[str, Any]:
        """The resolved `[share]` configuration (global-only; see config.py)."""
        return share_settings(load_config())

    def share_status(self, session_id: str) -> dict[str, Any]:
        settings = self.share_settings()
        record = self.share_store.get(session_id)
        can_revoke = bool(settings.get("revoke_endpoint")) and bool(
            record and record.get("share_id")
        )
        return {
            "configured": bool(settings.get("endpoint")),
            "can_revoke": can_revoke,
            "share": record,
        }

    def share_session(self, session_id: str) -> dict[str, Any]:
        """Upload the session's export to the configured endpoint and record the URL."""
        settings = self.share_settings()
        if not settings.get("endpoint"):
            return {
                "ok": False,
                "error": "Sharing needs a configured [share] endpoint.",
            }
        info = next(
            (
                row
                for row in self.list_sessions(None, include_children=True)
                if row.get("session_id") == session_id
            ),
            None,
        )
        messages = self.session_messages(session_id)
        if info is None and not messages:
            return {"ok": False, "error": "Session not found."}
        payload = {
            "info": info or {"session_id": session_id},
            "messages": messages,
        }
        result = upload_session(settings, payload)
        if not result.get("ok"):
            return result
        record = {
            "url": result["url"],
            "share_id": result.get("share_id", ""),
            "shared_at": result.get("shared_at", ""),
            "endpoint": settings.get("endpoint", ""),
        }
        self.share_store.set(session_id, record)
        return {"ok": True, **record}

    def unshare_session(self, session_id: str) -> dict[str, Any]:
        """Revoke a stored share, if the configuration supports a revoke at all."""
        settings = self.share_settings()
        share = self.share_store.get(session_id)
        if share is None:
            return {"ok": False, "error": "This session is not shared."}
        result = revoke_session(settings, share)
        if not result.get("ok"):
            return result
        self.share_store.clear(session_id)
        return {"ok": True}
