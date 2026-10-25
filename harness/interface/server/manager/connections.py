"""ConnectionsMixin: which connectors a session may use.

An account connects a connector, and it is on for every session unless that session switches it
off (`harness/integrations/connections.py`).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from harness.integrations.connections import effective as effective_connections
from harness.integrations.connectors import connector_list
from harness.modes.agents import DEFAULT_AGENT

logger = logging.getLogger("harness.manager")


class ConnectionsMixin:
    # -- what is effective ---------------------------------------------------------------

    def _connected_connectors(self) -> set[str]:
        """The names the account has connected, the first layer of the hierarchy."""
        return {row["name"] for row in connector_list(self.secrets) if row["connected"]}

    def effective_connectors(self, session_id: str) -> set[str]:
        """The connectors that are on for this session: connected, and not muted by the session's
        override. It drives the engine's connector-tool gating and the inbound delivery gate."""
        return set(
            effective_connections(
                connected=self._connected_connectors(),
                session_overrides=self.session_connections.get(session_id),
            )
        )

    def _inbound_connector_allowed(self, session_id: str, connector: str) -> bool:
        """Whether a message arriving on `connector` is delivered to this session. It uses the
        very set the engine gates connector tools with, so the two can never disagree: a muted
        connector is muted both ways from the first message."""
        return connector in self.effective_connectors(session_id)

    # -- the session side ----------------------------------------------------------------

    def _connection_detail(self, session_id: str, connector: str, info: Optional[dict[str, Any]]) -> str:
        """Why a connector is live for a session, in a few words: the chats it is subscribed to
        on that platform, plus "DMs" for the designated DM session. Channel names would need the
        live adapter's resolve cache, so the ids are shown, and a connector with neither falls
        back to its title."""
        prefix = f"{connector}:"
        parts = [
            sub.channel.split(":", 1)[1]
            for sub in self.subscriptions.for_session(session_id)
            if sub.channel.startswith(prefix)
        ]
        if self.dm_session() == session_id:
            parts.append("DMs")
        return " · ".join(parts) if parts else (info or {}).get("title") or connector

    def session_connections_view(self, session_id: str) -> dict[str, Any]:
        """The payload of the per-session connections drawer: every connector the account has
        connected, each with its effective state. A muted one stays visible as off, because a
        toggle must never make a row vanish. (`recommended` and `attention` stay in the payload,
        empty: they were a persona's recommendations.)"""
        rows = connector_list(self.secrets)
        by_name = {row["name"]: row for row in rows}
        connected_names = {row["name"] for row in rows if row["connected"]}
        effective = self.effective_connectors(session_id)
        connected = [
            {
                "connector": name,
                "kind": "connector",
                "enabled": name in effective,
                "detail": self._connection_detail(session_id, name, by_name.get(name)),
            }
            for name in sorted(connected_names)
        ]
        # MCP servers are sources too. A list built only from the connector registry showed a
        # freshly added server as live on the Connectors page and absent here, so the panel
        # that says what a session can touch has to name everything it can touch.
        connected.extend(self._mcp_sources(session_id))
        return {"connected": connected, "recommended": [], "attention": 0}

    def _mcp_sources(self, session_id: str) -> list[dict[str, Any]]:
        """The raw MCP servers this session gets, as rows of the sources list.

        They come from `_mcp_plan`, the same gating that arms the session, so the panel only
        ever shows what the agent really has. Connector-backed servers are skipped, since they
        already appear under their connector identity and a thing listed twice under two names
        is its own kind of untruth.

        These rows carry no per-session toggle: MCP has no per-session mute, and a switch that
        quietly meant "everywhere, for good" would be worse than none.
        """
        from harness.integrations.connectors.descriptors import get_descriptor

        workspace = self.engine_workspace(session_id, agent=DEFAULT_AGENT)
        try:
            plan = self._mcp_plan(session_id, workspace, DEFAULT_AGENT)
        except Exception:  # a broken config must not blank the whole panel
            logger.warning("could not read MCP config for the Sources list", exc_info=True)
            return []
        live = self.mcp.live_names()
        rows: list[dict[str, Any]] = []
        for server in plan:
            descriptor = get_descriptor(server.name)
            if descriptor is not None and descriptor.mcp_url:
                continue
            if server.name in live:
                # The count the Connectors page shows, from the same place.
                count = self.mcp.tool_count(server.name) or 0
                detail = f"{count} tool{'' if count == 1 else 's'}"
            elif self._mcp_errors.get(server.name):
                detail = "not connected"
            else:
                # Entitled but not dialled yet. The row's presence is the claim; a state nobody
                # has observed is not invented.
                detail = ""
            rows.append(
                {"connector": server.name, "kind": "mcp", "enabled": True, "detail": detail, "manageable": False}
            )
        return rows
