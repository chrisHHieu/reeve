"""The small HTTP endpoints that belong to no route family: health, shutdown, audit and the
like. Anything with real logic lives in `routes/`."""

from __future__ import annotations

import os
import sys
import threading
from typing import Any

from fastapi import FastAPI, Request

from .access import Gate


def register_lifecycle(app: FastAPI, manager, gate: Gate) -> None:
    @app.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        if gate.token and not gate.request_ok(request):
            return {"status": "ok"}
        return {"status": "ok", "default_workspace": manager.default_workspace, "model": manager.model}

    @app.post("/v1/shutdown")
    def shutdown(request: Request) -> dict[str, Any]:
        """Stop the server the way Ctrl+C would: the desktop shell's graceful quit.

        A plain kill never runs the lifespan teardown, so in-flight turns died unpersisted and
        MCP and LSP child trees outlived it. Setting `should_exit` lets uvicorn finish the
        request, stop accepting and run the teardown. With no server handle attached (a bare
        `create_app` in tests, an embedded run) it says so instead.
        """
        if gate.token and not gate.request_ok(request):
            return {"ok": False, "error": "unauthorized"}
        server = getattr(app.state, "uvicorn_server", None)
        if server is None:
            return {"ok": False, "error": "no server handle to stop"}
        server.should_exit = True
        return {"ok": True}

    @app.post("/v1/restart")
    def restart(request: Request) -> dict[str, Any]:
        """Start this server again from its own command line, so the code edited since it started
        is the code that runs.

        An agent working on Reeve restarted the server it ran in by hand: it started a second one
        in the background and killed the first by pid, eight times in one session, leaving stray
        processes on the port and two servers running one session. Here the same process stops
        the way `/v1/shutdown` stops it, so a running turn keeps its claim, and starts its
        successor once the port is free; the successor picks the turn up. The answer
        goes out first, so the command that asked gets its result. A desktop app's server is the
        app's to restart, and it says so."""
        if gate.token and not gate.request_ok(request):
            return {"ok": False, "error": "unauthorized"}
        server = getattr(app.state, "uvicorn_server", None)
        if server is None:
            return {"ok": False, "error": "no server handle to restart"}
        if getattr(sys, "frozen", False) or os.environ.get("REEVE_EXIT_WITH_PARENT") == "1":
            return {"ok": False, "error": "this server belongs to the desktop app: quit and reopen Reeve to restart it"}
        app.state.restart_requested = True
        threading.Timer(RESTART_AFTER_SECONDS, lambda: setattr(server, "should_exit", True)).start()
        return {
            "ok": True,
            "restarting": True,
            "note": "the server stops in a moment and starts again on the same port with the same token; "
            "a running turn is picked up where it stopped",
        }


#: How long `/v1/restart` waits before stopping, so its own answer reaches the caller.
RESTART_AFTER_SECONDS = 1.0


def register_observability(app: FastAPI, manager) -> None:
    @app.get("/v1/audit")
    def audit_list(
        limit: int = 100,
        session_id: str | None = None,
        connector: str | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        return {"events": manager.list_audit(limit=limit, session_id=session_id, connector=connector, tool=tool)}

    @app.get("/v1/workspace-branch")
    def workspace_branch_get(path: str) -> dict[str, Any]:
        return manager.workspace_branch(path)

    @app.get("/v1/browser-preview")
    async def browser_preview_get() -> dict[str, Any]:
        return await manager.browser_preview()


def register_attachment_probe(app: FastAPI) -> None:
    @app.post("/v1/attachments/inspect-pdf")
    def attachments_inspect_pdf(body: dict) -> dict[str, Any]:
        # The page count and size the composer checks against its threshold when a PDF is
        # attached. Local only.
        from harness.llm.pdf_support import inspect

        return inspect(str((body or {}).get("data_url", "")))


def register_debug_inject(app: FastAPI, manager) -> None:
    """Feed a message through the real inbound path, so the messaging stack can be exercised
    without a live bot connection. Registered only when `REEVE_DEBUG_INJECT=1`."""
    if os.environ.get("REEVE_DEBUG_INJECT") != "1":
        return

    @app.post("/v1/_debug/inject_inbound")
    async def debug_inject_inbound(body: dict) -> dict[str, Any]:
        from harness.integrations.connectors.messaging import MessageEvent, SessionSource

        event = MessageEvent(
            text=str((body or {}).get("text", "")),
            source=SessionSource(
                platform=str(body.get("platform", "slack")),
                chat_id=str(body.get("chat_id", "C0BD7KZ1AH5")),
                user_id=str(body.get("user_id", "U07JK68S4BH")),
                user_name=str(body.get("user_name", "tester")),
                chat_type=str(body.get("chat_type", "channel")),
                chat_name=str(body.get("chat_name", "")) or None,
                thread_id=str(body.get("thread_ts", "")) or None,
            ),
            message_id=str(body.get("ts", "")) or None,
            # Normally computed from the raw Slack text when the event is mapped.
            mentions_me=bool(body.get("mentions_me")),
        )
        await manager._dispatch_inbound(event)
        return {"ok": True}
