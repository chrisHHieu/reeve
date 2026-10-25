"""Which of a vendor MCP server's tool groups a connection serves (GitHub's toolsets)."""
from __future__ import annotations

from typing import Any


def register_connector_toolsets(app, manager):
    @app.post("/v1/connectors/{name}/toolsets")
    def connector_toolsets(name: str, body: dict) -> dict[str, Any]:
        """`body.enabled` is the list of toolset ids to serve.

        The reply lists the OAuth scopes the current token still lacks; the page offers
        "Grant access" (the ordinary mcp-connect) for those. Choosing never signs in.
        """
        ids = body.get("enabled")
        if not isinstance(ids, list):
            return {"ok": False, "error": "enabled must be a list of toolset ids"}
        return manager.set_connector_toolsets(name, [str(i) for i in ids])
