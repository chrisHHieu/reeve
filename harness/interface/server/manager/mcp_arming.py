"""McpArmingMixin: keeping a session's MCP tools in line with the configuration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from harness.extensions.mcp import MCP_GROUP, MCPServerDef, build_callables

logger = logging.getLogger("harness.manager")

#: Seconds a turn waits, in total, for its MCP servers to connect.
MCP_ARM_BUDGET_S = 20.0


def _stable_error(error: str) -> str:
    """An error text with per-process noise removed, for noticing change only: object
    addresses and long digit runs (pids, ports, timestamps) differ between identical failures
    across relaunches."""
    stable = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", error or "")
    return re.sub(r"\d{4,}", "N", stable)


class McpArmingMixin:
    async def arm_mcp(self, session_id: str, *, workspace: Optional[str] = None, agent: str = "reeve") -> list[Any]:
        """Bring a session's MCP tools in line with the configuration, at a turn boundary.

        Every path that starts a turn calls this. Before, MCP was attached only when an engine
        was BUILT, so a server added later never reached an open session, and three engine
        building paths (durable resume, out-of-band delivery, a Slack mention's new session)
        passed no MCP tools at all. The rule is now one sentence: a session uses the MCP servers
        that are enabled, from its next turn on.

        It returns the callables when the engine does not exist yet, for the caller to hand to
        `get_engine`. For a live engine it swaps them in place and returns []; the conversation
        is kept because the registry's schemas are read fresh each turn.

        It never raises. It sits in front of every turn, and an unreadable config would
        otherwise swallow the person's message with nothing to show for it. MCP may be broken;
        it may not cost a session. One guard here beats five at the call sites.
        """
        # The connectors ride the same door: every turn path passes here before it runs.
        self.resync_connectors(session_id)
        try:
            return await self._arm_mcp(session_id, workspace=workspace, agent=agent)
        except Exception:
            logger.warning("could not arm MCP for session %s; the turn runs without it", session_id, exc_info=True)
            return []

    async def _arm_mcp(self, session_id: str, *, workspace: Optional[str], agent: str) -> list[Any]:
        engine = self._engines.get(session_id)
        if engine is None:
            # No engine means no armed set. A signature left by an earlier engine of this id
            # (a rebind, a promotion, a moved folder) is dropped here, the only reader of
            # `_mcp_armed`, so the rebuild arms fully and no eviction site has to remember.
            self._mcp_armed.pop(session_id, None)
            return await self.prepare_mcp_tools(session_id, workspace=workspace, agent=agent)

        root = self.engine_workspace(session_id, workspace=workspace, agent=agent)
        signature = self._mcp_signature(self._mcp_plan(session_id, root, agent), session_id)
        if signature == self._mcp_armed.get(session_id):
            # Nothing changed: no network, and no churn on the model's tool list. A connect the
            # last arm did not wait for may have failed since, and that is said here.
            self._say_mcp_failures(engine, session_id)
            return []

        tools = await self.prepare_mcp_tools(session_id, workspace=workspace, agent=agent)
        added, removed, refused = engine.registry.sync_group(MCP_GROUP, tools)
        if added:
            # A server never seen before joins a turn late, and what the thread shows the
            # session had loaded from it is loaded again now that the tools exist.
            from harness.tools.discovery import restore_loaded

            restore_loaded(engine.registry, getattr(engine, "messages", None))
        if added or removed:
            logger.info("mcp re-armed for %s: +%d -%d", session_id, len(added), len(removed))
        for name in refused:
            # A remote server tried to take a name the local toolset owns and the registry kept
            # the local one. The tool the person just connected is genuinely missing, and the
            # reason cannot be guessed from outside.
            engine._append_notice(
                "mcp_error", f"MCP tool “{name}” was ignored — that name is already taken by a built-in tool"
            )
        # A re-arm can fail the way a first arm can, and a session that quietly lacks the server
        # the person just added is the bug this exists to fix.
        self._say_mcp_failures(engine, session_id)
        return []

    def resync_connectors(self, session_id: str) -> None:
        """A live session's connector tools follow the Connectors page: a connector connected,
        disconnected, or a tool switched since the last turn is in (or out of) this one, without a
        new session or a restart (`compose/features/connectors.py`). An engine not built yet will
        be built with what is connected now. Never raises: a connector problem may not cost a turn."""
        engine = self._engines.get(session_id)
        if engine is None:
            return
        refresh = getattr(engine.registry, "connector_resync", None)
        if refresh is None:  # an agent with no connectors
            return
        try:
            added, removed = refresh(self.effective_connectors(session_id))
        except Exception:
            logger.warning("could not refresh connector tools for session %s", session_id, exc_info=True)
            return
        if added:
            # What the thread shows was loaded from a connector that went and came back is
            # loaded again, as a re-armed MCP server's tools are.
            from harness.tools.discovery import restore_loaded

            restore_loaded(engine.registry, getattr(engine, "messages", None))
        if added or removed:
            logger.info("connector tools refreshed for %s: +%d -%d", session_id, len(added), len(removed))

    def _say_mcp_failures(self, engine: Any, session_id: str) -> None:
        for name, error in self.pop_mcp_failures(session_id):
            detail = f": {error}" if error else ""
            engine._append_notice("mcp_error", f"MCP server “{name}” failed to start{detail}"[:500], server=name)

    async def prepare_mcp_tools(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "reeve"
    ) -> list[Any]:
        """The tool callables of the servers this session is entitled to.

        What a session is entitled to is configuration (`_mcp_plan`). Whether a server is
        connected right now does not decide what the session is told: one that has answered a
        tool list before is stated from that list (`mcp/tool_cache.py`) and the turn does not
        wait for it. Its connection is made in the background, or by the first call to one of
        its tools. Only a server never seen on this machine is waited for, inside one budget
        for the whole arm, and it joins from the next turn if it is not up in time.

        A server that fails to connect is recorded either way. It keeps its stated tools unless
        what it needs is a person signing in again, since a tool that can only answer "sign in"
        is not worth offering. Prefer `arm_mcp`, which adds the "did anything change" gate and
        can attach to a live engine; this is the build-time path the tests drive directly.
        """
        root = self.engine_workspace(session_id, workspace=workspace, agent=agent)
        loop = asyncio.get_running_loop()
        out: list[Any] = []
        plan = self._mcp_plan(session_id, root, agent)
        # One budget for the whole arm. It sits in front of the turn, and a server whose first
        # start fetches a package held it at "Working…" with nothing sent. The connect is
        # shielded, so a slow server keeps starting in the background.
        deadline = loop.time() + MCP_ARM_BUDGET_S
        for server in plan:
            # A per-session server (the built-in browser) connects under this session, so two
            # sessions never share tabs or logins; everything else connects once for the app.
            scope = session_id if server.per_session else ""
            known = self.mcp_tool_lists.load(server)
            pending = self._mcp_pending.get((scope, server.name))
            if pending is None or pending.done():
                pending = asyncio.ensure_future(self.mcp.ensure(server, scope=scope))
                self._mcp_pending[(scope, server.name)] = pending
            if known is not None:
                # Stated from the list it last answered with; the connect ends beside the turn,
                # and a watcher hears how: a newer list is kept, a failure is recorded, and a
                # sign-in needed forgets the list.
                self._watch_mcp_connect(pending, session_id, server, scope)
                tools = known
            else:
                try:
                    connection = await asyncio.wait_for(
                        asyncio.shield(pending), timeout=max(0.0, deadline - loop.time())
                    )
                except asyncio.TimeoutError:
                    logger.info("mcp %s is still starting; its tools join next turn", server.name)
                    continue
                except Exception as exc:
                    self._note_mcp_failure(session_id, server, scope, exc)
                    continue
                self._note_mcp_connected(server, connection)
                tools = connection.tools
            out.extend(
                build_callables(
                    server,
                    tools,
                    lambda tool, args, name=server.name, scope=scope, server=server: self.mcp.call(
                        name, tool, args, scope=scope, server=server
                    ),
                    loop,
                )
            )
        # Recorded AFTER connecting, because the signature reads the live set: a server that
        # just came up is part of the armed state and one that just failed is not, which is what
        # makes the next turn skip a doomed reconnect instead of paying its timeout again.
        self._mcp_armed[session_id] = self._mcp_signature(plan, session_id)
        return out

    def _note_mcp_connected(self, server: MCPServerDef, connection: Any) -> None:
        """A server answered: clear what was held against it and keep its tool list."""
        self._mcp_errors.pop(server.name, None)
        # Recovery resets the notice dedupe, so a server that breaks again later is announced
        # again in the next session.
        self._clear_mcp_notified(server.name)
        self.mcp_tool_lists.save(server, getattr(connection, "tools", None))

    def _note_mcp_failure(
        self, session_id: str, server: MCPServerDef, scope: str, error: BaseException
    ) -> None:
        """Record a failed connect for the MCP page and for the session's notice."""
        from harness.extensions.mcp import oauth as mcp_oauth

        if mcp_oauth.is_auth_required(error):
            # The stored tokens no longer refresh (the vendor rotated or expired them) and the
            # non-interactive connect refused to open a browser. Recorded so the MCP page shows
            # WHY the server is dark; the session simply runs without its tools.
            self._mcp_errors[server.name] = "sign-in required — reconnect this server from its page"
            logger.info("mcp %s needs re-auth; skipped for this session", server.name)
            # Its kept list goes too: until a person signs in again this is not a server whose
            # tools can be stated, and the next arm must not offer them from memory.
            self.mcp_tool_lists.forget(server.name)
        else:
            # A bad command, a crashed child or an unreachable URL must not be silent.
            message = str(error) or error.__class__.__name__
            tail = self.mcp.last_stderr(server.name, scope=scope)
            if tail:
                message = f"{message} — {tail}"
            self._mcp_errors[server.name] = message[:500]
            logger.warning("mcp %s failed to connect: %s", server.name, message[:500])
        # The transcript is told when the state CHANGES and not while it persists: a server that
        # stays broken stamps only the first session after it broke (or broke differently), and
        # the Connectors page carries the standing error.
        if self._should_notify_mcp_failure(server.name, self._mcp_errors.get(server.name, "")):
            self._mcp_session_failures.setdefault(session_id, []).append(server.name)

    def _watch_mcp_connect(self, pending: Any, session_id: str, server: MCPServerDef, scope: str) -> None:
        """Hear how a connect the turn did not wait for ended. Without it, a known server that
        no longer starts would keep its tools on the list and say nothing anywhere."""
        key = (scope, server.name)
        if key in self._mcp_watched:
            return
        self._mcp_watched.add(key)

        def ended(future: Any) -> None:
            self._mcp_watched.discard(key)
            try:
                if future.cancelled():
                    return
                error = future.exception()
                if error is None:
                    self._note_mcp_connected(server, future.result())
                else:
                    self._note_mcp_failure(session_id, server, scope, error)
            except Exception:  # noqa: BLE001 - bookkeeping in a callback must not raise
                logger.debug("mcp %s: connect outcome not recorded", server.name, exc_info=True)

        pending.add_done_callback(ended)

    def _should_notify_mcp_failure(self, name: str, error: str) -> bool:
        """True once per failure episode: the first session after `name` starts failing, or
        after its error text changes. It is kept in the prefs so a relaunch does not repeat the
        same complaint, and compared on a normalised text, because stderr often embeds
        per-process values that made "the same" failure look new on every relaunch."""
        stable = _stable_error(error)
        notified = self._prefs.setdefault("mcp_notified_errors", {})
        if notified.get(name) == stable:
            return False
        notified[name] = stable
        self._save_prefs()
        return True

    def _clear_mcp_notified(self, name: str) -> None:
        if self._prefs.get("mcp_notified_errors", {}).pop(name, None) is not None:
            self._save_prefs()

    def pop_mcp_failures(self, session_id: str) -> list[tuple[str, Optional[str]]]:
        """Drain (name, error) of the servers that failed while this session's tools were
        prepared, once, for the socket to write its transcript notice."""
        names = self._mcp_session_failures.pop(session_id, [])
        return [(name, self._mcp_errors.get(name)) for name in names]
