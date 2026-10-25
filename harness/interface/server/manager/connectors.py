"""ConnectorsMixin: the connector list as the Connectors page shows it, and its lifecycle."""

from __future__ import annotations

from typing import Any, Optional

from harness.extensions.mcp import tool_name
from harness.integrations.connectors import (
    connect_connector,
    connector_list,
    disconnect_connector,
    set_experimental_enabled,
    update_connector_tools,
)


def _mcp_kind(tool: Any) -> str:
    """read, write or destructive, as the SERVER declares it.

    MCP tools carry `annotations` (readOnlyHint, destructiveHint), and a vendor that sets them
    classifies its own catalog, which retired the hand-written read/write table we used to keep.
    A server that annotates nothing reads as "write": unknown intent is the cautious side, and
    it only changes how the row is labelled.
    """
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return "write"
    if getattr(annotations, "destructiveHint", False):
        return "destructive"
    return "read" if getattr(annotations, "readOnlyHint", False) else "write"


class ConnectorsMixin:
    def _names_of(self, connector: str, user_ids) -> dict[str, Any]:
        """Display names for user ids, from the people directory (the ids stay the truth)."""
        return {uid: self._people.get(f"{connector}:{uid}") for uid in (user_ids or [])}

    def _live_tools(self, row: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
        """The tools of a connector that sits on an MCP server. It declares none of its own: the
        vendor's server is the catalog, so the page shows what the LIVE connection returned, with
        no pin and nothing filtered. None means it is not connected, so there is nothing to list yet."""
        live = self.mcp.conn(row["name"])
        if live is None:
            return None
        return [
            {
                "name": tool_name(row["name"], tool.name),
                "label": (getattr(tool, "annotations", None) and tool.annotations.title) or tool.name,
                # The server classifies itself, and we report what it says.
                "kind": _mcp_kind(tool),
                "description": (getattr(tool, "description", "") or "")[:300],
                "enabled": True,
                "requires_approval": False,
            }
            for tool in live.tools
        ]

    def list_connectors(self) -> list[dict[str, Any]]:
        # Two-way connectors are enriched with the live gateway's recent senders, so the page can
        # manage the allow-list inline, each recent sender flagged authorised or not.
        rows = connector_list(self.secrets)
        for row in rows:
            name = row["name"]
            # Why a one-click sign-in did not finish. `list_mcp` hides connector-backed servers
            # from the MCP tab, which left this failure nowhere to appear: the dialog said
            # "Check your browser…" and polled forever while the reason sat unread in
            # `_mcp_errors`. A button waiting on something already known to have failed lies.
            if name in self._mcp_errors:
                row["mcp_error"] = self._mcp_errors[name]
            row["mcp_authorizing"] = name in self._mcp_authorizing
            if not row.get("tools"):
                tools = self._live_tools(row)
                if tools is not None:
                    row["tools"] = tools
            if not (row.get("two_way") and row.get("connected")):
                continue
            allowed = set(row.get("allowed_users") or [])
            recent = self.gateway.recent_senders(name) if self.gateway else []
            for sender in recent:
                sender["authorized"] = sender.get("user_id") in allowed
                # Backfilled from the people directory, since an event may predate name scopes.
                sender["user_name"] = sender.get("user_name") or self._people.get(f"{name}:{sender.get('user_id')}")
            row["recent"] = recent
            # Messages from unknown senders that were parked, which the page resolves inline.
            row["unauthorized"] = self.parked.list(name)
            row["allowed_user_names"] = self._names_of(name, row.get("allowed_users"))
            for workspace in row.get("workspaces") or []:
                workspace["allowed_user_names"] = self._names_of(name, workspace.get("allowed_users"))
        return rows

    def connect_connector(self, name: str, fields: dict[str, Any], *, acknowledged: bool = False) -> dict[str, Any]:
        # Validates the token with a live API call (blocking), so callers run it off the loop.
        return connect_connector(self.secrets, name, fields, acknowledged=acknowledged)

    def set_experimental_connectors(self, value: bool) -> dict[str, Any]:
        return set_experimental_enabled(self.secrets, value)

    def disconnect_connector(self, name: str) -> dict[str, Any]:
        # For a connector on an MCP server the live connection goes before the tokens do, entry
        # and all and not just a shutdown flag. A flag leaves the connection in the cache until
        # its task unwinds, and a Connect pressed in that window is handed the revoked
        # connection and reports success without any sign-in.
        self.mcp.forget(name)
        from harness.integrations.whatsapp.connector import NAME as WHATSAPP_PERSONAL

        if name == WHATSAPP_PERSONAL:
            # A linked device is unlinked from the phone, and what was kept here goes with it.
            self.whatsapp_unlink()
        return disconnect_connector(self.secrets, name)

    def update_connector_tools(self, name: str, enabled: dict[str, Any]) -> dict[str, Any]:
        return update_connector_tools(self.secrets, name, enabled)

    def list_audit(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self.audit_store.list(limit=limit, session_id=session_id, connector=connector, tool=tool)
