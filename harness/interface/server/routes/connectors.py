"""Routes of the Connectors page: connecting, disconnecting, allow-lists and the Slack rosters."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI

from .connector_accounts import register_connector_accounts


def register_connectors(
    app: FastAPI,
    manager,
    *,
    reload_listeners: Callable[[str], Awaitable[None]],
    spawn: Callable[..., Any],
) -> None:
    """`reload_listeners(name)` makes a two-way platform pick up new credentials at once;
    `spawn(coro, what=...)` runs a long sign-in in the background."""

    @app.get("/v1/connectors")
    def connectors_list() -> dict[str, Any]:
        return {"connectors": manager.list_connectors()}

    @app.post("/v1/connectors/{name}/connect")
    async def connector_connect(name: str, body: dict) -> dict[str, Any]:
        fields = body.get("fields") if isinstance(body, dict) else None
        # An experimental connector requires the caller to acknowledge its risk notice.
        acknowledged = bool(isinstance(body, dict) and body.get("acknowledge_risk"))
        # Validating the token makes a blocking HTTP call, which must stay off the event loop.
        result = await asyncio.to_thread(
            lambda: manager.connect_connector(name, fields or {}, acknowledged=acknowledged)
        )
        if not result.get("ok"):
            return result
        await reload_listeners(name)
        # A pasted token is a bearer and these vendors' MCP servers accept one, so the manual
        # path reaches the same catalog one-click does, with no OAuth App to register first. A
        # failure is reported and not fatal: the connector keeps its REST tools.
        from harness.integrations.connectors.descriptors import get_descriptor

        descriptor = get_descriptor(name)
        if descriptor is not None and descriptor.mcp_url and descriptor.mcp_device_auth:
            armed = await manager.arm_bearer_mcp(name)
            if armed.get("ok"):
                result["mcp_tools"] = armed.get("tools")
            else:
                result["mcp_error"] = armed.get("error")
        return result

    @app.post("/v1/connectors/{name}/mcp-connect")
    async def connector_mcp_connect(name: str, body: Optional[dict] = None) -> dict[str, Any]:
        # One-click connect. The browser flow can take minutes, so it runs in the background and
        # the GUI polls /v1/connectors until the card flips to connected.
        #
        # `body.variant` picks among a multi-endpoint server's endpoints. It is optional and has
        # to stay optional: every other connector posts no body at all, and requiring one would
        # break all of them at once.
        from harness.integrations.connectors.descriptors import get_descriptor

        variant = str((body or {}).get("variant") or "").strip() or None
        descriptor = get_descriptor(name)
        if descriptor is None:
            return {"ok": False, "error": f"unknown connector: {name}"}
        if getattr(descriptor, "browser_token_auth", False):
            # The vendor's own authorize page hands back a token (Trello).
            spawn(manager.connect_token_grant(name), what=f"token grant {name}")
            return {"ok": True, "started": True}
        if not descriptor.mcp_url and getattr(descriptor, "pkce_auth", False):
            # Browser sign-in for a connector with tools of our own (Google Workspace): the same
            # grant, but the token lands in the connector profile and seeds no MCP server.
            spawn(manager.connect_pkce_connector(name), what=f"pkce connect {name}")
            return {"ok": True, "started": True}
        if not descriptor.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        if descriptor.mcp_device_auth:
            # A device grant mints a code the person must paste, so this half is awaited: the
            # screen needs it now. Only the polling runs detached.
            return await manager.mcp_connect_connector(name, variant)
        spawn(manager.mcp_connect_connector(name, variant), what=f"one-click connect {name}")
        return {"ok": True, "started": True}

    # Trello's one-click landing: Trello sends the browser here with the token in the
    # fragment; the page posts it back with the flow's state. Both answer without the sidecar
    # token, which a browser redirect cannot carry.
    from harness.integrations.connectors.trello_auth import CALLBACK_PATH as TRELLO_CALLBACK

    @app.get(TRELLO_CALLBACK)
    async def trello_landing() -> Any:
        from fastapi.responses import HTMLResponse

        from harness.integrations.connectors.trello_auth import GRANT, landing_script

        from .browser_page import browser_page

        page = browser_page("Finishing the Trello sign-in…", "One moment.", ok=True)
        page = page.replace("</body>", landing_script(GRANT.state or "") + "</body>")
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.post(TRELLO_CALLBACK)
    async def trello_token(body: dict) -> dict[str, Any]:
        from harness.integrations.connectors.trello_auth import GRANT

        body = body if isinstance(body, dict) else {}
        return {"ok": GRANT.deliver(str(body.get("token") or ""), str(body.get("state") or ""))}

    @app.post("/v1/connectors/whatsapp_personal/pair")
    async def whatsapp_pair(body: dict) -> dict[str, Any]:
        # Linking a personal WhatsApp number: the daemon starts and offers a QR code,
        # or the code to type for `phone`. The risk notice must have been acknowledged.
        body = body if isinstance(body, dict) else {}
        return await asyncio.to_thread(
            manager.whatsapp_pair, str(body.get("phone") or ""), bool(body.get("acknowledged"))
        )

    @app.get("/v1/connectors/whatsapp_personal/pair")
    async def whatsapp_pair_status() -> dict[str, Any]:
        return await asyncio.to_thread(manager.whatsapp_pair_status)

    # What the linked device keeps on this computer: how much, for how long, and clearing it
    # without unlinking.
    @app.get("/v1/connectors/whatsapp_personal/kept")
    async def whatsapp_kept() -> dict[str, Any]:
        return await asyncio.to_thread(manager.whatsapp_kept)

    @app.put("/v1/connectors/whatsapp_personal/kept")
    async def whatsapp_set_keep(body: dict) -> dict[str, Any]:
        body = body if isinstance(body, dict) else {}
        # null keeps every message; any other value is checked against the choices offered.
        return await asyncio.to_thread(manager.whatsapp_set_keep, body.get("keep_days"))

    @app.delete("/v1/connectors/whatsapp_personal/kept")
    async def whatsapp_clear_kept() -> dict[str, Any]:
        return await asyncio.to_thread(manager.whatsapp_clear_kept)

    @app.post("/v1/connectors/{name}/disconnect")
    async def connector_disconnect(name: str) -> dict[str, Any]:
        result = manager.disconnect_connector(name)
        await reload_listeners(name)
        return result

    register_connector_accounts(app, manager)

    @app.post("/v1/connectors/{name}/unauthorized/{item_id}")
    async def connector_unauthorized_resolve(name: str, item_id: str, body: dict) -> dict[str, Any]:
        # Settles a parked message from an unknown sender: dismiss, allow, or allow and deliver.
        return await manager.resolve_unauthorized(name, item_id, str((body or {}).get("action", "")).strip())

    @app.patch("/v1/connectors/{name}/tools")
    def connector_tools_patch(name: str, body: dict) -> dict[str, Any]:
        enabled = (body or {}).get("enabled")
        if not isinstance(enabled, dict):
            return {"ok": False, "error": "enabled map required"}
        return manager.update_connector_tools(name, enabled)

    @app.post("/v1/connectors/{name}/allow")
    def connector_allow(name: str, body: dict) -> dict[str, Any]:
        # `name` is optional and seeds the people directory, so a person picked from the
        # directory shows their display name before they have ever sent a message.
        return manager.allow_user(name, str(body.get("user_id", "")), display_name=str(body.get("name", "")))

    @app.get("/v1/connectors/slack/directory")
    async def slack_directory(q: str = "", limit: int = 25) -> dict[str, Any]:
        """The workspace's member roster for the people picker. Cached on this machine and never
        sent anywhere."""
        from harness.integrations.connectors import slack_directory as roster

        return await asyncio.to_thread(lambda: roster.list_members(manager.secrets, q, limit))

    @app.get("/v1/connectors/slack/channels")
    async def slack_channels(q: str = "", limit: int = 25) -> dict[str, Any]:
        """The channel roster for the typeahead: every public channel, and private ones only where
        the bot is a member (a Slack constraint)."""
        from harness.integrations.connectors import slack_directory as roster

        return await asyncio.to_thread(lambda: roster.list_channels(manager.secrets, q, limit))

    @app.post("/v1/connectors/{name}/disallow")
    def connector_disallow(name: str, body: dict) -> dict[str, Any]:
        return manager.disallow_user(name, str(body.get("user_id", "")))

