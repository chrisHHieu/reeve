"""Routes of the MCP servers: the list, their configuration, connecting, and the OAuth landing."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .browser_page import browser_page


def register_mcp(app: FastAPI, manager, *, spawn: Callable[..., Any]) -> None:
    @app.get("/v1/mcp")
    def mcp_list() -> dict[str, Any]:
        return {"servers": manager.list_mcp()}

    @app.get("/v1/browser/config")
    def browser_config() -> dict[str, Any]:
        # The backend picker of the built-in `browser` server (Connectors page): the current
        # choice, the options and the credential state. One backend at a time.
        return manager.browser_config()

    @app.put("/v1/browser/config")
    def browser_config_set(body: dict) -> dict[str, Any]:
        return manager.set_browser_config(body or {})

    @app.post("/v1/mcp")
    def mcp_add(body: dict) -> dict[str, Any]:
        name = body.get("name")
        config = body.get("config")
        if not name or not isinstance(config, dict):
            return {"ok": False, "error": "name and config required"}
        return manager.add_mcp(name, config)

    @app.patch("/v1/mcp/{name}")
    def mcp_patch(name: str, body: dict) -> dict[str, Any]:
        return manager.patch_mcp(name, body or {})

    @app.delete("/v1/mcp/{name}")
    def mcp_delete(name: str) -> dict[str, Any]:
        return manager.delete_mcp(name)

    @app.get("/v1/mcp/{name}/tools")
    async def mcp_tools(name: str) -> dict[str, Any]:
        return await manager.mcp_tools(name)

    @app.post("/v1/mcp/{name}/connect")
    async def mcp_connect(name: str) -> dict[str, Any]:
        # Connect now. For an OAuth server the first connect opens the system browser and waits
        # on the loopback callback, which can take minutes, so it runs as a background task and
        # the GUI polls /v1/mcp for the status to flip (authorizing → connected, or needs_auth
        # with a last_error).
        manager.begin_mcp_connect(name)  # so "authorizing" shows on the very next poll
        spawn(manager.connect_mcp(name), what=f"mcp connect {name}")
        return {"ok": True, "started": True}

    @app.get("/v1/mcp/{name}/authorize-url")
    async def mcp_authorize_url(name: str) -> dict[str, Any]:
        """The sign-in URL of the flow that is waiting, so the person has a way back.

        `webbrowser.open` can fail without a sound: no exception, no default browser, or a process
        not attached to the desktop session. The connect then waits on a callback for a page that
        never appeared and nothing on screen offers a second chance. Every sign-in shape stores
        its URL in one place, so a single route covers dynamic registration and a
        pre-registered client alike.
        """
        from harness.extensions.mcp import oauth as mcp_oauth

        return {
            "ok": True,
            "authorizing": name in manager._mcp_authorizing,
            "authorize_url": mcp_oauth.last_authorize_url() or "",
        }

    @app.post("/v1/mcp/{name}/signout")
    async def mcp_signout(name: str) -> dict[str, Any]:
        return await manager.signout_mcp(name)

    @app.get("/mcp/oauth/callback")
    async def mcp_oauth_callback(code: str = "", state: str = "", error: str = "") -> Any:
        # The loopback landing of the MCP OAuth browser flow. It faces the browser and returns the
        # same styled card as the managed-connector callbacks.
        from harness.extensions.mcp import oauth as mcp_oauth

        if error:
            page = browser_page(
                "Sign-in failed",
                "The service reported an error. Return to Reeve and try again.",
                ok=False,
                error=error,
            )
            return HTMLResponse(page, status_code=400)
        if not code or not mcp_oauth.deliver_callback(code, state or None):
            page = browser_page(
                "Nothing waiting for this sign-in",
                "The sign-in may have timed out. Return to Reeve and start it again.",
                ok=False,
            )
            return HTMLResponse(page, status_code=400)
        return HTMLResponse(
            browser_page("Connected", "Sign-in complete. You can close this tab and return to Reeve.", ok=True)
        )

    @app.post("/v1/mcp/reload")
    async def mcp_reload() -> dict[str, Any]:
        return await manager.reload_mcp()
