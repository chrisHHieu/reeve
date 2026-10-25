"""PrefsMixin: the small preferences file, and the one preference that routes direct messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


class PrefsMixin:
    def _prefs_path(self) -> Path:
        return self._data_base / "prefs.json"

    def _load_prefs(self) -> dict[str, Any]:
        try:
            return json.loads(self._prefs_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_prefs(self) -> None:
        self._prefs_path().write_text(json.dumps(self._prefs, indent=2), encoding="utf-8")

    def dm_session(self) -> Optional[str]:
        """The session a direct message to the bot is routed to, as the person designated it.
        None means direct messages are parked."""
        return self._prefs.get("dm_session") or None

    def set_dm_session(self, session_id: Optional[str]) -> dict[str, Any]:
        """Designate the session that handles incoming direct messages, or clear it with a
        falsy id."""
        wanted = (session_id or "").strip()
        if wanted:
            self._prefs["dm_session"] = wanted
        else:
            self._prefs.pop("dm_session", None)
        self._save_prefs()
        return {"ok": True, "dm_session": self.dm_session()}

    # -- The default shell (amendment of 2026-10-14) ------------------------
    # What a command that names no shell runs in, for sessions built from now on and for the
    # terminal panel. "" is the OS's own. A session keeps the one it was built with: `run_shell`'s
    # schema names it, and that schema is part of the cached prefix.
    def default_shell(self) -> str:
        from harness.tools.shell_kinds import preferred

        return preferred()

    def shell_choices(self) -> list[dict[str, str]]:
        """The shells this machine has that may be the default, as {kind, label}."""
        from harness.tools.shell_kinds import PREFERABLE, local_shells

        found = local_shells()
        return [{"kind": kind, "label": found[kind].label} for kind in PREFERABLE if kind in found]

    def set_default_shell(self, kind: Any) -> dict[str, Any]:
        from harness.tools.shell_kinds import normalise, set_preferred

        wanted = normalise(str(kind or ""))
        if wanted and wanted not in {choice["kind"] for choice in self.shell_choices()}:
            return {"ok": False, "error": f"{wanted!r} is not a shell this machine can make the default"}
        self._prefs["shell"] = set_preferred(wanted)
        self._save_prefs()
        return {"ok": True, "shell": self.default_shell()}
