"""SessionPersistMixin: writing a session's record, and restoring what it holds."""

from __future__ import annotations

import logging
from typing import Any

from harness.loop import TurnEngine
from harness.session.conversations import title_from
from harness.session.sessions import SessionRecord

from .anchor import session_workspace
logger = logging.getLogger("harness.manager")


def grants_of(engine) -> dict[str, Any]:
    """The engine's session-scoped "Always allow" approvals, in a shape that can be saved."""
    permissions = engine.permissions
    tools = sorted(getattr(permissions, "session_allow_tools", None) or ())
    commands = sorted(getattr(permissions, "session_allow_commands", None) or ())
    if not (tools or commands):
        return {}
    return {"tools": tools, "commands": commands}


class SessionPersistMixin:
    def save(self, session_id: str, engine: TurnEngine) -> None:
        world = getattr(engine, "world", None)
        remote = world is not None and getattr(world, "is_remote", False)
        if remote:
            # A remote world's cwd is a POSIX path on another machine. `realpath` would mangle it
            # into a local spelling, and it does not belong in the local "recent workspaces"
            # either. The world root is the session anchor, since the executor's cwd may have
            # moved during the turn.
            executor = getattr(engine, "executor", None)
            workspace = str(getattr(world, "root", "") or (executor.cwd if executor else ""))
        else:
            # The primary root, not the shell's cwd, which follows every `cd`.
            workspace = session_workspace(engine) or ""
        record = SessionRecord(
            session_id=session_id,
            workspace=workspace,
            model=engine.model,
            mode=engine.permissions.mode.value,
            # The plan overlay is a separate axis, saved beside the preset so a session reloaded
            # mid-rehearsal stays in plan.
            plan=bool(engine.permissions.plan),
            thinking=getattr(engine, "thinking_level", None),
            messages=engine.messages,
            # With no messages there is no snapshot: "New session" must never reach the store as
            # a real title, since it would win the upsert's COALESCE for good.
            title=title_from(engine.messages) if engine.messages else "",
            agent=getattr(engine, "agent_name", "code"),
            extra_roots=self._extra_roots_of(engine, session_id),
            grants=grants_of(engine),
            compaction=engine.compaction_state.as_dict() if getattr(engine, "compaction_state", None) else {},
        )
        # Only the remote path passes `touch`; a local save keeps the plain one-argument call so
        # stores that are duck-typed (tests) stay compatible.
        if remote:
            self.session_store.save(record, touch=False)
        else:
            self.session_store.save(record)
        # Fold the session's event log into the metrics store (debounced, best effort). It runs
        # after the record write so the fold sees the fresh model and agent.
        self._record_session_metrics(session_id)

    async def flush_session(self, session_id: str, engine: TurnEngine) -> bool:
        """Make everything this turn has done durable, and say whether it landed.

        The durability gate in front of a consequential tool call awaits this
        (`harness/session/durability.py`). `save` is already the durable write, synchronous and
        append-only, so this adds an answer and not a mechanism. It answers False instead of
        raising so that the gate can refuse the call with a sentence the model can use, and not
        an ERROR the model reads as a broken tool.
        """
        try:
            self.save(session_id, engine)
            return True
        except Exception:
            logger.warning("could not durably record session %s before a side effect", session_id, exc_info=True)
            return False

    @staticmethod
    def _apply_grants(engine: TurnEngine, grants: dict[str, Any]) -> None:
        """Re-apply the "Always allow" approvals a reloaded session had been given. They are
        scoped to the session, and the session outlives the process."""
        for tool in grants.get("tools") or []:
            engine.permissions.allow_tool_for_session(str(tool))
        for command in grants.get("commands") or []:
            engine.permissions.allow_command_for_session(str(command))
        # A saved `readonly` grant is ignored: what it allowed runs with no ask now.

    def _extra_roots_of(self, engine: TurnEngine, session_id: str) -> list[dict[str, Any]]:
        """The folders the person or the agent added: the engine's roots minus the primary (the
        first) and minus the session's provisioned scratch root. Saving the scratch as an extra
        would add it back as a plain folder on every rebuild.

        A remote session's extras are POSIX paths on the other host, plain dicts and not
        RootDirs. Each row carries `remote: true`, so the grant survives the per-turn save while
        a local session can never adopt it."""
        roots = getattr(engine, "roots", None) or []
        if getattr(getattr(engine, "world", None), "is_remote", False):
            return [
                {
                    "path": str(self._root_field(root, "path", "")),
                    "writable": bool(self._root_field(root, "writable", False)),
                    "label": str(self._root_field(root, "label", "") or ""),
                    "remote": True,
                    **({"reached": True} if self._root_field(root, "reached", False) else {}),
                }
                for root in roots[1:]
                if str(self._root_field(root, "path", "")).startswith("/")
            ]
        scratch = (self.scratch_base() / session_id).expanduser()
        try:
            scratch = scratch.resolve()
        except OSError:
            pass
        return [
            root.to_dict()
            for root in roots[1:]
            if root.path != scratch and not getattr(root, "managed", False)
        ]
