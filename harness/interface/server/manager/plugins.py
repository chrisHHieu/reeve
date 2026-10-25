"""`/reload-plugins`: rebuild one conversation with the plugins as they are now (design
§10.1).

A session's plugins are fixed when its engine is built — the skill catalog, the agent list,
hooks, MCP servers, language servers and `bin/` all read them then, so the system prompt and
the prompt cache stay stable. Turning a plugin on, installing one or agreeing to its code
reaches a conversation already open only through this reload: between turns, the engine is
dropped (its transcript is saved first and read back by the next build), every view of the
session is told to reconnect, and the next build logs `plugins/active` with
`reason: "reload"`.

The cost is stated to the person before they ask for it: the next reply re-reads the whole
history (the system prompt changed, so no prompt cache), and commands the agent left running
in its terminals stop with the engine that owns them (one execution world per session, ADR
0003 — the new engine cannot adopt the old one's processes).
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Auto-update (design §8.4): the first pass waits for startup to settle, then once a day.
#: A pass that finds a turn running waits and tries again — it runs while the app is idle.
PLUGIN_UPDATE_FIRST_DELAY = 300.0
PLUGIN_UPDATE_EVERY = 24 * 3600.0
PLUGIN_UPDATE_BUSY_RETRY = 600.0


class PluginsMixin:
    def start_plugin_updates(self, download: Optional[Callable[[str], bytes]] = None) -> None:
        if getattr(self, "_plugin_update_task", None) is None:
            self._plugin_update_task = asyncio.create_task(self._plugin_update_loop(download))

    async def stop_plugin_updates(self) -> None:
        task = getattr(self, "_plugin_update_task", None)
        self._plugin_update_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _plugin_update_loop(self, download: Optional[Callable[[str], bytes]]) -> None:
        from harness.extensions.plugins.updates import auto_update

        await asyncio.sleep(PLUGIN_UPDATE_FIRST_DELAY)
        while True:
            if getattr(self, "_running_sessions", None) or getattr(self, "shutting_down", False):
                await asyncio.sleep(PLUGIN_UPDATE_BUSY_RETRY)
                continue
            try:
                report = await asyncio.to_thread(auto_update, download=download)
                if any(report.values()):
                    logger.info(
                        "plugin auto-update: %d updated, %d waiting, %d withdrawn, %d failed",
                        *(len(report[k]) for k in ("updated", "waiting", "flagged", "errors")),
                    )
            except Exception:  # an update pass must never take the server down
                logger.exception("plugin auto-update pass failed")
            await asyncio.sleep(PLUGIN_UPDATE_EVERY)

    def _retired_engines(self) -> "weakref.WeakSet[Any]":
        retired = getattr(self, "_plugin_retired", None)
        if retired is None:
            retired = self._plugin_retired = weakref.WeakSet()
        return retired

    def engine_retired(self, engine: Any) -> bool:
        """Whether a socket still holding `engine` must reconnect before its next turn: the
        engine was replaced by a plugin reload."""
        return engine in self._retired_engines()

    def take_plugin_reload(self, session_id: str) -> bool:
        """True once for the first engine built after a reload of this session."""
        pending = getattr(self, "_plugin_reloads", None)
        if not pending or session_id not in pending:
            return False
        pending.discard(session_id)
        return True

    async def reload_plugins(self, session_id: str) -> dict[str, Any]:
        if self.is_running(session_id):
            return {"ok": False, "error": "a turn is running — reload the plugins when it ends"}
        engine = self._engines.pop(session_id, None)
        if engine is not None:
            self.save(session_id, engine)
            self._retired_engines().add(engine)
            from .session_roots import _close_engine_terminals

            _close_engine_terminals(engine)
        pending = getattr(self, "_plugin_reloads", None)
        if pending is None:
            pending = self._plugin_reloads = set()
        pending.add(session_id)
        from harness.extensions.plugins import resolve_plugins

        record = self.session_store.load(session_id)
        workspace = (record.workspace if record else None) or self.default_workspace
        plugins = resolve_plugins(workspace=workspace)
        summary = {"plugins": len(plugins.plugins), "errors": len(plugins.errors)}
        # `views`: how many live views were told. None is a real case, not only a closed
        # app: a socket still building its engine (a cold start, MCP servers connecting) is
        # not registered yet — it will get the new plugins anyway, but the person who asked
        # hears nothing unless the caller says it itself (seen live on :1420, 2026-09-28).
        views = len(self._session_clients.get(session_id, ()))
        await self.broadcast_session(session_id, {"type": "plugins_reloaded", "data": summary})
        return {"ok": True, **summary, "views": views}
