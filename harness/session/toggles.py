"""Per-conversation on/off switches over named things (skills, connectors), kept in one JSON file.

The file holds only what a conversation changed: `{"sessions": {session_id: {name: bool}}}`. A
name a conversation never touched is absent, and absent means "as the rest of the app says".
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional


class SessionToggles:
    """What each conversation switched on or off. With no path it lives in memory only."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._guard = threading.Lock()
        self._by_session: dict[str, dict[str, bool]] = self._read()

    def _read(self) -> dict[str, dict[str, bool]]:
        if self.path is None:
            return {}
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8")).get("sessions", {})
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(stored, dict):
            return {}
        return {str(sid): {str(name): bool(on) for name, on in (flags or {}).items()} for sid, flags in stored.items()}

    def _write(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sessions": self._by_session}, indent=2), encoding="utf-8")

    def get(self, session_id: str) -> dict[str, bool]:
        """A copy of this conversation's switches; changing it changes nothing stored."""
        return dict(self._by_session.get(session_id, {}))

    def set(self, session_id: str, name: str, enabled: bool) -> None:
        with self._guard:
            self._by_session.setdefault(session_id, {})[name] = bool(enabled)
            self._write()

    def clear(self, session_id: str, name: str) -> None:
        """Hand one name back to the default; a conversation left with no switches is dropped."""
        with self._guard:
            flags = self._by_session.get(session_id, {})
            if name not in flags:
                return
            flags.pop(name)
            if not flags:
                self._by_session.pop(session_id, None)
            self._write()

    def remove_session(self, session_id: str) -> None:
        """A deleted conversation takes its switches with it."""
        with self._guard:
            if self._by_session.pop(session_id, None) is not None:
                self._write()
