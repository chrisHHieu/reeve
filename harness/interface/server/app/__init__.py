"""The HTTP and WebSocket app every surface rides on.

`create_app` assembles it: authentication in front, the route families from `routes/`, the
OpenAI-compatible endpoint, and two sockets. `/ws/session/{id}` carries one session's event
stream and the person's side of it (`session_socket.py`); `/ws/events` is the app-wide push
channel. The GUI, the IDE and the messaging bridges all talk to this one app.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..manager import SessionManager
from ..routes import (
    register_automations,
    register_child_worktrees,
    register_connector_toolsets,
    register_connectors,
    register_core,
    register_inbox,
    register_mcp,
    register_memory,
    register_metrics,
    register_plugins,
    register_preview,
    register_projects,
    register_providers,
    register_remote,
    register_sessions,
    register_settings,
    register_share,
    register_skills,
    register_slack_user_token,
    register_terminal,
    register_workspaces,
)
from .access import ORIGIN_PATTERN, Gate, origin_allowed
from .background import TaskKeeper
from .endpoints import (
    register_attachment_probe,
    register_debug_inject,
    register_lifecycle,
    register_observability,
)
from .limits import MAX_FRAME_BYTES
from .openai_compat import register_openai
from .session_socket import SessionSocket

__all__ = ["MAX_FRAME_BYTES", "create_app"]

# These paths answer without the sidecar token: the health probe, and the OAuth redirect, which
# comes from the person's browser and cannot carry it.
_TOKENLESS = {"/v1/health", "/mcp/oauth/callback", "/connectors/trello/callback"}


async def _reload_listeners_for(manager, name: str) -> None:
    """New or removed credentials take effect only when a platform socket reconnects (Socket
    Mode authenticates at connect time), so the listeners reload in-process and pasting a token
    works at once, without a sidecar restart."""
    from harness.integrations.connectors.config import PLATFORMS

    if name in PLATFORMS:
        try:
            await manager.refresh_gateway()
        except Exception:
            pass  # a listener that fails to come up must not fail the save


def _sweep_scratch(manager) -> None:
    try:
        removed = manager.sweep_unused_scratch()
        if removed:
            print(f"[reeve] removed {removed} empty scratch folder(s) no conversation owns")
    except Exception:  # housekeeping must never stop the server
        traceback.print_exc()


def _lifespan(manager):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            live = await manager.start_gateway()  # the messaging listeners, if any are configured
            if live:
                print(f"[reeve] messaging gateway live: {', '.join(live)}")
        except Exception:  # a bad connector must never stop the server
            traceback.print_exc()
        try:
            # The personal-WhatsApp daemon, once an account is linked.
            if await asyncio.to_thread(manager.start_whatsapp):
                print("[reeve] whatsapp (personal) live")
        except Exception:
            traceback.print_exc()
        from ..routes.plugins import guarded_download

        # Plugin auto-update is off per marketplace until a person turns it on, and its loop is
        # idle work that never runs while a turn does.
        manager.start_plugin_updates(download=guarded_download)
        # The turns and background commands the last process did not finish, picked
        # up beside the server rather than before it answers: the GUI connects meanwhile and
        # sees them run.
        manager.start_resume_interrupted()
        # The empty scratch folders no conversation owns, off the loop: housekeeping, never in
        # the way of the first answer.
        asyncio.get_running_loop().run_in_executor(None, _sweep_scratch, manager)
        yield
        await manager.stop_plugin_updates()
        await manager.aclose()  # stops the gateway and closes MCP connections

    return lifespan


def _require_token(app: FastAPI, gate: Gate) -> None:
    @app.middleware("http")
    async def require_sidecar_token(request: Request, call_next):
        # A page served by the preview door never talks to this API, token or no.
        if gate.refused_origin(request.headers.get("origin")):
            return JSONResponse({"error": "a previewed page cannot use the Reeve API"}, status_code=403)
        # A preflight carries the requested header's name and not its value; CORS checks the
        # Origin, and the real request still has to authenticate.
        if (
            not gate.token
            or request.method == "OPTIONS"
            or request.url.path in _TOKENLESS
            or gate.request_ok(request)
        ):
            return await call_next(request)
        return JSONResponse({"error": "missing or invalid Reeve sidecar token"}, status_code=401)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=ORIGIN_PATTERN.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _register_sockets(app: FastAPI, manager, gate: Gate, keeper: TaskKeeper) -> None:
    @app.websocket("/ws/session/{session_id}")
    async def ws_session(ws: WebSocket, session_id: str) -> None:
        await SessionSocket(ws, manager, keeper, session_id).serve(gate)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """The app-wide event stream, independent of any session. The GUI keeps one open for
        pushes such as an automation starting. Read-only: inbound frames are ignored, and the
        receive loop only notices the disconnect."""
        if not await gate.admit(ws):
            return
        manager.register_event_client(ws.send_json)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_event_client(ws.send_json)


def create_app(manager: SessionManager) -> FastAPI:
    keeper = TaskKeeper()
    gate = Gate(os.environ.get("REEVE_API_TOKEN", ""))
    # Read each time: the door has no address until its first grant.
    gate.never = lambda: getattr(getattr(manager, "_preview_door", None), "listening", None)
    spawn = keeper.spawn

    app = FastAPI(title="reeve", version="0.0.0", lifespan=_lifespan(manager))
    # Exposed so that a task surviving collection can be tested rather than asserted from the
    # source: a task collected mid-flight is cancelled silently, which nothing else would catch.
    app.state.spawn = spawn
    app.state.background = keeper.tasks
    app.state.manager = manager
    _require_token(app, gate)

    register_lifecycle(app, manager, gate)
    register_core(app, manager)
    register_metrics(app, manager)
    register_inbox(app, manager)
    register_skills(app, manager)
    register_plugins(app, manager)
    register_workspaces(app, manager)
    register_sessions(app, manager)
    register_preview(app, manager)
    register_child_worktrees(app, manager)
    register_memory(app, manager)
    register_projects(app, manager)
    register_openai(app, manager)
    register_mcp(app, manager, spawn=spawn)
    register_connectors(
        app, manager, reload_listeners=lambda name: _reload_listeners_for(manager, name), spawn=spawn
    )
    register_connector_toolsets(app, manager)
    register_slack_user_token(app, manager)
    register_observability(app, manager)
    register_providers(app, manager, spawn=spawn)
    register_settings(app, manager)
    register_attachment_probe(app)
    register_automations(app, manager)
    register_terminal(
        app,
        manager,
        websocket_authenticated=gate.socket_ok,
        origin_allowed=origin_allowed,
        sidecar_token=gate.token,
    )
    register_share(app, manager)
    register_remote(app, manager)
    register_debug_inject(app, manager)
    _register_sockets(app, manager, gate, keeper)
    return app
