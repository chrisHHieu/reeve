"""Folders granted on a remote host: checked on the host and kept as POSIX paths.

A session bound to an SSH environment cannot be asked `Path.is_dir()` locally. A grant is validated
with one stat on the host, lives on the engine's shared roots as a plain dict, and is persisted with
`remote: true` so a local session can never adopt it.
"""

from __future__ import annotations

import posixpath
from typing import Any

from harness.orchestration.remote_env.transport import RemoteEnvError

_PRIMARY_REMOVE_REMOTE = "cannot remove the primary workspace directory"


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


class RemoteRootsMixin:
    """Expects `RootsMixin`'s helpers and the manager's `session_store`, `_engines` and `remote_environments`."""

    def _remote_session_for(self, environment_id: str) -> Any:
        """A bare `RemoteSession` for a bound profile, with no probe and no world.

        The manager's host-side checks (a directory grant's target, one stat command) need the same
        destination and runner seam a session build uses. Building the transport is enough, and no
        session state is created.
        """
        store = self.remote_environments
        if store is None:
            raise ValueError("remote environments are not available in this session host")
        profile = store.get(environment_id)
        from harness.orchestration.remote_env import RemoteSession

        return RemoteSession(
            profile,
            runner=getattr(store, "_runner", None),
            password=store.load_password(environment_id),
        )

    @staticmethod
    def _remote_directory_kind(session: Any, path: str) -> str:
        """`d`, `f`, `e` or `n` for one remote path. It is the same one-probe primitive the remote
        file backend uses, so a grant is checked against the real host."""
        from harness.orchestration.remote_env import RemoteFileBackend

        return RemoteFileBackend(session, path).stat_kind(path)

    def _remote_extra_roots(self, record: Any) -> list[dict[str, Any]]:
        """A remote record's persisted granted roots (rows tagged remote only)."""
        return [
            {
                "path": str(row.get("path")),
                "writable": bool(row.get("writable", False)),
                "label": str(row.get("label") or ""),
                "remote": True,
                **({"reached": True} if row.get("reached") else {}),
            }
            for row in ((record.extra_roots if record else []) or [])
            if isinstance(row, dict) and row.get("remote") is True and str(row.get("path", "")).startswith("/")
        ]

    def _check_remote_directory(self, record: Any, path: str):
        """`(resolved path, None)` when `path` is a directory on the session's host, else
        `(None, the error result)`."""
        environment_id = str(getattr(record, "environment_id", "") or "")
        try:
            resolved = self._validate_remote_workspace(path)
        except ValueError as error:
            return None, _failure(str(error))
        try:
            kind = self._remote_directory_kind(self._remote_session_for(environment_id), resolved)
        except KeyError:
            return None, _failure(f"No remote environment with id '{environment_id}'.")
        except ValueError as error:
            return None, _failure(str(error))
        except RemoteEnvError as error:
            return None, _failure(error.tool_error)
        if kind != "d":
            return None, _failure(f"not a directory: {path}")
        return resolved, None

    def _add_remote_root(
        self, session_id: str, record: Any, path: str, writable: bool, reached: bool = False
    ) -> dict[str, Any]:
        """Grant access to a directory ON THE SESSION'S HOST.

        The path is checked on the host (absolute POSIX, an existing directory) and then appended to
        the engine's shared roots as a plain POSIX dict. That list is what the remote file backend
        and the boundary geometry both read, so reads, and writes for a writable grant, reach it at
        once. The persisted row is tagged `remote: true`, so a grant made on a remote session can
        never be adopted by a local one, nor the reverse. `reached` is as in `add_root`: it only
        widens, and keeps whoever owned the folder.
        """
        resolved, failure = self._check_remote_directory(record, path)
        if failure:
            return failure
        label = posixpath.basename(resolved.rstrip("/")) or resolved
        writable = bool(writable)
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None) is not None:
            known = [r for r in engine.roots if str(self._root_field(r, "path", "")) == resolved]
            for root in known:
                if isinstance(root, dict):
                    root["writable"] = (reached and bool(root.get("writable"))) or writable
                    root["label"] = root.get("label") or label
                    if not reached:
                        root.pop("reached", None)
                else:
                    root.writable = (reached and root.writable) or writable
                    if not reached:
                        root.reached = False
            if not known:
                entry = {"path": resolved, "writable": writable, "label": label}
                engine.roots.append({**entry, "reached": True} if reached else entry)
            self._persist_live_roots(session_id, engine)
        else:
            stored = self._remote_extra_roots(record)
            matching = [entry for entry in stored if entry["path"] == resolved]
            for entry in matching:
                entry["writable"] = (reached and entry["writable"]) or writable
                if not reached:
                    entry.pop("reached", None)
            if not matching:
                entry = {"path": resolved, "writable": writable, "label": label, "remote": True}
                stored.append({**entry, "reached": True} if reached else entry)
            self.session_store.set_extra_roots(session_id, stored)
        return {"ok": True, "roots": self.get_roots(session_id), "notice": None}

    def _remove_remote_root(self, session_id: str, record: Any, path: str) -> dict[str, Any]:
        """Revoke a granted remote folder. The session's own workspace cannot be removed."""
        try:
            resolved = self._validate_remote_workspace(path)
        except ValueError as error:
            return _failure(str(error))
        roots = self._live_roots(session_id)
        if roots is not None:
            if str(self._root_field(roots[0], "path", "")) == resolved:
                return _failure(_PRIMARY_REMOVE_REMOTE)
            if any(str(self._root_field(r, "path", "")) == resolved for r in roots):
                roots[:] = [r for r in roots if str(self._root_field(r, "path", "")) != resolved]
                self._persist_live_roots(session_id, self._engines[session_id])
            return {"ok": True, "roots": self.get_roots(session_id)}
        kept = [entry for entry in self._remote_extra_roots(record) if entry["path"] != resolved]
        self.session_store.set_extra_roots(session_id, kept)
        return {"ok": True, "roots": self.get_roots(session_id)}

    def _stored_remote_rows(self, record: Any) -> list[dict[str, Any]]:
        workspace = record.workspace or ""
        rows = [
            {"path": workspace, "writable": True, "label": "workspace", "primary": True, "exists": True, "remote": True}
        ]
        for entry in self._remote_extra_roots(record):
            if entry["path"] != workspace:
                label = entry["label"] or posixpath.basename(entry["path"].rstrip("/"))
                rows.append(
                    {
                        "path": entry["path"],
                        "writable": entry["writable"],
                        "label": label,
                        "primary": False,
                        "exists": True,
                        "remote": True,
                        **({"reached": True} if entry.get("reached") else {}),
                    }
                )
        return rows
