"""ProjectsMixin: the named projects a session's memory is bound to."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from harness.session.projects import project_key, project_label, resolve_memory_key

_KINDS = ("memory",)


class ProjectsMixin:
    #: The memory key every per-conversation scratch folder shares.
    SCRATCH_MEMORY_KEY = "general"

    def _memory_key_for(self, record, ws: Optional[str]) -> Optional[str]:
        """Binding > scratch > git > path, with the one-time path→git re-key on the way.

        A conversation with no project runs in its own scratch folder, and keying memory by
        that path gave every such conversation a private memory no later one could read:
        13 dead directories, ~200 files, on the audited install (2026-09-24). They share
        one key instead — the rule is one memory per place you work, and "no
        project" is one place."""
        binding = ((record.bindings if record else {}) or {}).get("memory")
        if not binding and ws and self._is_scratch_workspace(ws):
            return self.SCRATCH_MEMORY_KEY
        return resolve_memory_key(
            ws,
            binding=binding,
            names=self.session_store.names(),
        )

    def _is_scratch_workspace(self, ws: str) -> bool:
        try:
            return Path(ws).expanduser().resolve().is_relative_to(
                self.scratch_base().expanduser().resolve()
            )
        except (OSError, ValueError):
            return False

    def _workspace_of(self, session_id: str) -> Optional[str]:
        record = self.session_store.load(session_id)
        return (record.workspace if record else None) or self.default_workspace

    def project_menu(self, session_id: str, kind: str) -> dict[str, Any]:
        """The payload of the project submenu: the project derived from the session's folder
        (pinned, and labelled by the usual rules) and the named ones, most recently used first.
        The GUI shows five and grows a filter at six, so the full list ships and the filter
        reaches all of it."""
        record = self.session_store.load(session_id)
        workspace = self._workspace_of(session_id)
        derived = project_key(workspace) if workspace else None
        return {
            "kind": kind,
            "bound": ((record.bindings if record else {}) or {}).get(kind),
            "derived": {**project_label(derived), "key": derived} if derived else None,
            "named": [{"name": entry["name"], "key": entry["key"]} for entry in self.session_store.names().list(kind)],
        }

    def set_binding(self, session_id: str, kind: str, name: Optional[str]) -> dict[str, Any]:
        """Bind a named project to this session, or unbind it with `name=None`. It takes effect
        at the next engine build: the running engine keeps what it started with, the doctrine
        that memory deletions follow too."""
        if kind not in _KINDS:
            return {"ok": False, "error": f"unknown kind {kind!r}"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        if name and self.session_store.names().resolve(kind, name) is None:
            return {"ok": False, "error": f"no {kind} named {name!r}"}
        record = self.session_store.load(session_id)
        if record is None:
            return {"ok": False, "error": "unknown session"}
        bindings = dict(record.bindings or {})
        if name:
            bindings[kind] = name
        else:
            bindings.pop(kind, None)
        self.session_store.set_bindings(session_id, bindings)
        # The cached engine is dropped so the next turn rebuilds with the new key. The messages
        # persist through the record.
        self._engines.pop(session_id, None)
        return {"ok": True, "bindings": bindings}

    def name_current_project(self, session_id: str, kind: str, name: str) -> dict[str, Any]:
        """Give the session's derived project a name the person chose ("Name current…")."""
        workspace = self._workspace_of(session_id)
        if not workspace:
            return {"ok": False, "error": "session has no workspace"}
        try:
            entry = self.session_store.names().name_current(kind, name, project_key(workspace))
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, **entry}
