"""The directories a session can touch: its scratch or workspace, plus folders the person adds.

A live session keeps them on its engine (a shared list that permissions, the file tools and the
context injector all read). A session that is not running keeps them on its stored record, and a
session bound to a remote host keeps POSIX paths that only the host can answer for.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from harness.config.roots import RootDir
from harness.session.projects import project_key, project_label, project_presence
from harness.session.sessions import SessionRecord

from .anchor import session_workspace
from .remote_roots import RemoteRootsMixin

_PRIMARY_REMOVE_LOCAL = "cannot remove the primary scratch directory"


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _stored_root(row: dict[str, Any]) -> dict[str, Any]:
    """The fields a persisted extra root keeps: `reached` only on a root the session took."""
    stored = {"path": row["path"], "writable": row["writable"], "label": row.get("label", "")}
    return {**stored, "reached": True} if row.get("reached") else stored


def _within(inner: Path, outer: Path) -> bool:
    return inner != outer and inner.is_relative_to(outer)


class RootsMixin(RemoteRootsMixin):
    """Expects the manager's `session_store`, `_engines`, `get`-style helpers and scratch provisioning."""

    @staticmethod
    def _root_field(root: Any, name: str, default: Any = None) -> Any:
        """One attribute of a `RootDir` or of a plain root dict (remote roots are dicts)."""
        if isinstance(root, dict):
            return root.get(name, default)
        return getattr(root, name, default)

    def _live_roots(self, session_id: str) -> Optional[list]:
        """The running engine's shared root list, or None when it has none."""
        engine = self._engines.get(session_id)
        roots = getattr(engine, "roots", None) if engine is not None else None
        return roots if roots else None

    def _persist_live_roots(self, session_id: str, engine: Any) -> None:
        self.session_store.set_extra_roots(session_id, self._extra_roots_of(engine, session_id))

    def is_remote_session(self, session_id: str) -> bool:
        """True when the session's record carries an SSH environment binding."""
        record = self.session_store.load(session_id)
        return bool(record is not None and getattr(record, "environment_id", None))

    # -- reading the list ---------------------------------------------------------

    def _root_present(self, path: str) -> bool:
        """Whether a root is there to use: a folder on disk, or a scratch folder, which is named
        when a session is built and made when its first turn starts (`_scratch_path`), so not yet
        made is not "Not found"."""
        return Path(path).is_dir() or self.is_temp_workspace(path)

    def _engine_root_rows(self, engine: Any) -> list[dict[str, Any]]:
        remote = bool(getattr(getattr(engine, "world", None), "is_remote", False))
        rows: list[dict[str, Any]] = []
        for index, root in enumerate(engine.roots):
            if getattr(root, "managed", False):
                continue  # harness-owned (the memory directory), not one of the person's folders
            path = str(self._root_field(root, "path", ""))
            rows.append(
                {
                    "path": path,
                    "writable": bool(self._root_field(root, "writable", False)),
                    "label": self._root_field(root, "label", "") or "",
                    "primary": index == 0,
                    "exists": True if remote else self._root_present(path),
                    **({"remote": True} if remote else {}),
                    **({"reached": True} if self._root_field(root, "reached", False) else {}),
                }
            )
        return rows

    def _stored_local_rows(self, session_id: str, record: Any) -> list[dict[str, Any]]:
        primary = record.workspace if record and record.workspace else self._scratch_path(session_id)
        primary_is_scratch = self.is_temp_workspace(primary)
        rows = [
            {
                "path": primary,
                "writable": True,
                "label": "scratch" if primary_is_scratch else "workspace",
                "primary": True,
                "exists": self._root_present(primary),
            }
        ]
        # A real-folder session also carries its provisioned scratch root, which matches the shape a
        # live engine reports, so a cold read looks like a live one.
        if not primary_is_scratch and self._SESSION_ID_RE.match(session_id or ""):
            scratch = self.scratch_base() / session_id
            rows.append(
                {
                    "path": str(scratch.expanduser().resolve()),
                    "writable": True,
                    "label": "scratch",
                    "primary": False,
                    "exists": True,  # a scratch folder is made at the first turn, never missing
                }
            )
        for stored in (record.extra_roots if record else []) or []:
            path = str(stored.get("path", ""))
            rows.append(
                {
                    "path": path,
                    "writable": bool(stored.get("writable", False)),
                    "label": stored.get("label") or Path(path).name,
                    "primary": False,
                    "exists": Path(path).is_dir(),
                    **({"reached": True} if stored.get("reached") else {}),
                }
            )
        return rows

    def get_roots(self, session_id: str) -> list[dict[str, Any]]:
        """The directories this session can touch: the primary first, then added folders.

        A running engine is read live; otherwise the list is rebuilt from the stored record. A remote
        session's roots are POSIX paths on the other host, so `exists` is reported True (an ssh round
        trip would be needed to know) and each row carries `"remote": True`, letting a surface label
        it instead of trying to open it locally.
        """
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None):
            return self._engine_root_rows(engine)
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            return self._stored_remote_rows(record)
        return self._stored_local_rows(session_id, record)

    # -- changing the list --------------------------------------------------------

    def promote_workspace(self, session_id: str, path: str) -> dict[str, Any]:
        """Adopt `path` as the session's primary workspace.

        It happens once and in one direction: only while the primary is still the provisioned
        scratch, and a session that already has a real workspace is never re-pointed. The live
        session changes (roots and shell cwd), the change is persisted, and the engine is marked for
        a rebuild after the turn.
        """
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            return _failure("this session works on a remote host; there is no local folder to promote")
        folder = Path(path).expanduser()
        if not folder.is_dir():
            return _failure(f"not a directory: {path}")
        resolved = folder.resolve()
        engine = self._engines.get(session_id)
        if engine is None:
            return _failure("no live session to promote")
        executor = getattr(engine, "executor", None)
        current = session_workspace(engine)
        if not current or not self.is_temp_workspace(current):
            return _failure("this session already has a workspace")
        roots = getattr(engine, "roots", None)
        if roots is None:
            return _failure("this session has no directory list")
        # The list is shared, so permissions, file tools and the context injector see the new primary
        # at once, and the old scratch primary stays on as the scratch root.
        roots[:] = [
            RootDir(path=resolved, writable=True, label="workspace"),
            *[r for r in roots if r.path != resolved],
        ]
        try:
            # Move the live shell too, so the next command starts in the new workspace. `save`
            # persists roots[0] (anchor.py), which was set just above.
            result = executor.run(f"cd {shlex.quote(str(resolved))}", timeout=15)
            if result.get("exit_code") != 0:
                executor.cwd = str(resolved)
        except Exception:
            executor.cwd = str(resolved)  # a respawned shell starts there
        self.save(session_id, engine)
        self.session_store.touch_workspace(str(resolved))
        self._promotion_rebuild.add(session_id)
        return {"ok": True, "path": str(resolved), "roots": self.get_roots(session_id)}

    def _stored_extras(self, session_id: str, drop: Path) -> list[dict[str, Any]]:
        """The persisted extra roots of a session that is not running, without `drop` and without the
        session's own scratch."""
        session_scratch = (self.scratch_base() / session_id).expanduser().resolve()
        return [
            row
            for row in self.get_roots(session_id)
            if not row["primary"] and Path(row["path"]).resolve() not in (drop, session_scratch)
        ]

    def add_root(
        self, session_id: str, path: str, writable: bool = False, *, reached: bool = False
    ) -> dict[str, Any]:
        """Grant the session access to another folder, read-only or read-write.

        A running engine changes in place (file tools, permissions and context see it at once) and
        the grant is persisted so a later resume still has it. On a remote session the grant is
        validated and listed ON THE HOST and lands as a remote root.

        `reached` is the session's own add, a call that reached the folder's project and was allowed.
        It only ever widens: an existing root keeps its access or gains write, and keeps
        whoever owned it. Folders it took earlier inside the same project fold into it, so a
        project read file by file is one folder. A person's add owns the folder: it sets the access
        they chose and clears `reached`.
        """
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            return self._add_remote_root(session_id, record, path, writable, reached)
        folder = Path(path).expanduser()
        if not folder.is_dir():
            return _failure(f"not a directory: {path}")
        resolved = folder.resolve()
        writable = bool(writable)
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None) is not None:
            present = [r for r in engine.roots if r.path == resolved]
            for root in present:
                if reached:
                    root.writable = root.writable or writable
                else:
                    root.writable = writable  # already there: only its access level changes
                    root.reached = False
            if reached:
                engine.roots[1:] = [
                    r for r in engine.roots[1:] if not (getattr(r, "reached", False) and _within(r.path, resolved))
                ]
            if not present:
                engine.roots.append(RootDir(path=resolved, writable=writable, reached=reached))
            self._persist_live_roots(session_id, engine)
        else:
            # A brand-new conversation has no record until its first turn is saved, so make one now:
            # `set_extra_roots` needs a row to update and the folder must survive.
            if self.session_store.load(session_id) is None:
                self.session_store.save(
                    SessionRecord(
                        session_id=session_id,
                        workspace=self._provision_scratch(session_id),
                        model=self.model,
                        mode=self.mode.value,
                        messages=[],
                        agent="reeve",  # folder access is the agent's
                    )
                )
            before = {Path(row["path"]).resolve(): row for row in self._stored_extras(session_id, Path())}
            extras = self._stored_extras(session_id, resolved)
            if reached:
                extras = [
                    row for row in extras if not (row.get("reached") and _within(Path(row["path"]).resolve(), resolved))
                ]
            prior = before.get(resolved)
            if reached and prior is not None:
                writable = bool(prior.get("writable")) or writable
                reached = bool(prior.get("reached"))
            row = {"path": str(resolved), "writable": writable, "label": resolved.name}
            extras.append({**row, "reached": True} if reached else row)
            self.session_store.set_extra_roots(session_id, [_stored_root(row) for row in extras])
        self.session_store.touch_workspace(str(resolved))
        # If the directory's project already has memory, say so once, as a pointer, to
        # the agent and the person.
        notice = self._project_notice(str(resolved), session_id)
        engine = self._engines.get(session_id)
        if notice and engine is not None:
            engine._append_notice("project_presence", notice)
        return {"ok": True, "roots": self.get_roots(session_id), "notice": notice}

    def _project_notice(self, path: str, session_id: str = "") -> Optional[str]:
        """A one-line pointer for a newly granted directory whose project already has memory, or
        None.

        Never for the session's OWN project: the line "'wealthlog' already has project memory — bind
        it by name or start a session there" was once said inside a wealthlog session
        (b3a8491e-9db), pointing the owner at the place they already were.
        """
        try:
            key = project_key(path)
            engine = self._engines.get(session_id) if session_id else None
            own_workspace = session_workspace(engine) if engine is not None else None
            if own_workspace:
                record = self.session_store.load(session_id)
                if key in {project_key(own_workspace), self._memory_key_for(record, own_workspace)}:
                    return None
            presence = project_presence(key, memory=self._auto_memory_for(key))
        except Exception:
            return None
        if not presence["memories"]:
            return None
        label = project_label(key)["label"]
        return (
            f"“{label}” already has project memory ({presence['memories']} entries) — bind it by "
            "name or start a session there to use it."
        )

    def remove_root(self, session_id: str, path: str) -> dict[str, Any]:
        """Revoke a folder that was added. The primary scratch cannot be removed."""
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            return self._remove_remote_root(session_id, record, path)
        resolved = Path(path).expanduser().resolve()
        roots = self._live_roots(session_id)
        if roots is not None:
            if roots[0].path == resolved:
                return _failure(_PRIMARY_REMOVE_LOCAL)
            roots[:] = [r for r in roots if r.path != resolved]
            self._persist_live_roots(session_id, self._engines[session_id])
        else:
            current = self.get_roots(session_id)
            if current and current[0]["primary"] and Path(current[0]["path"]).resolve() == resolved:
                return _failure(_PRIMARY_REMOVE_LOCAL)
            kept = self._stored_extras(session_id, resolved)
            self.session_store.set_extra_roots(session_id, [_stored_root(row) for row in kept])
        return {"ok": True, "roots": self.get_roots(session_id)}
