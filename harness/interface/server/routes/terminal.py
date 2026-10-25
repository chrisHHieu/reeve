"""The user's terminal panel (`/terminal`): REST create/attach/close + a live WS channel.

A PTY panel sits behind `/terminal`
(, a `pty.create` on
open at). This is the honest equivalent over the shell
substrate this project already has (`harness/tools/terminal.py`): one dedicated
pipe-backed shell per session, attached over `WS /ws/terminal/{session_id}`. The
limitation is that substrate's own, stated there and in the panel's UI: no PTY, so no
full-screen TUI programs.

The WS rides the sidecar's existing authentication: the launch token as a
`sec-websocket-protocol` entry (accepted back as `reeve`), the same Origin gate the
session socket uses. Frames are JSON in both directions:

    → {"type": "input", "data": "ls\r\n"}   raw keystrokes
    → {"type": "interrupt"}                  Ctrl-C to the foreground process
    → {"type": "resize", "data": {"cols": 120, "rows": 30}}
    ← {"type": "terminal", "data": {…status, output, produced}}   attach/replay
    ← {"type": "output", "data": {"chunk": "…"}}                  live output (ANSI)
    ← {"type": "exit", "data": {"exit_code": …}}                  terminal exited

The channel is now a REAL pty (`harness/tools/pty_session.py`): output is the terminal's
own byte stream (ANSI escapes included) for an xterm.js host, not a plain text pipe.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..manager import SessionManager

#: One input frame may carry this many characters. A panel sends keystrokes; the cap
#: stops a crafted frame from turning into an unbounded stdin write.
MAX_TERMINAL_INPUT_CHARS = 8192


def register_terminal(
    app: FastAPI,
    manager: SessionManager,
    *,
    websocket_authenticated: Callable[[WebSocket], bool],
    origin_allowed: Callable[[str | None], bool],
    sidecar_token: str,
) -> None:
    @app.post("/v1/sessions/{session_id}/terminal")
    def open_user_terminal(session_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Open or attach one of the session's terminals. The body carries the client's
        `{"terminal": id, "shell": id, "cols": n, "rows": n}` — the shell menu's pick and
        the emulator's window size. Returns the terminal's scrollback + status."""
        payload = body or {}
        return manager.user_terminal(
            session_id,
            shell=payload.get("shell"),
            terminal_id=str(payload.get("terminal") or "1"),
            cols=payload.get("cols"),
            rows=payload.get("rows"),
        )

    @app.get("/v1/sessions/{session_id}/terminal/shells")
    def user_terminal_shells(session_id: str) -> dict[str, Any]:
        """The shells this machine can launch — the guide menu's discovery (the
        `launchShells`), so the picker only ever offers what the backend can spawn."""
        return manager.user_terminal_shells(session_id)

    @app.get("/v1/sessions/{session_id}/terminal")
    def user_terminal_status(session_id: str, terminal: str = "1") -> dict[str, Any]:
        return manager.user_terminal(session_id, terminal_id=terminal)

    @app.delete("/v1/sessions/{session_id}/terminal")
    def close_user_terminal(session_id: str, terminal: str = "1") -> dict[str, Any]:
        return manager.user_terminal_close(session_id, terminal_id=terminal)

    @app.websocket("/ws/terminal/{session_id}")
    async def ws_terminal(ws: WebSocket, session_id: str) -> None:
        if not websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        # WebSockets bypass CORS, and this one writes into a shell: a cross-site page must
        # not be able to open it. Same gate as the session socket (`app.py:574-579`).
        if not origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="reeve" if sidecar_token else None)
        # One session can hold several terminals; the panel passes its tab's id.
        terminal_id = str(ws.query_params.get("terminal") or "1")

        queue: asyncio.Queue[tuple[str, int, int]] = asyncio.Queue(maxsize=4096)
        loop = asyncio.get_running_loop()

        def deliver(chunk: str, start: int, end: int) -> None:
            # Called on the shell's reader thread. Dropping on a full queue is the honest
            # failure: the terminal stays alive and the next snapshot still has scrollback.
            def push() -> None:
                try:
                    queue.put_nowait((chunk, start, end))
                except asyncio.QueueFull:
                    pass

            loop.call_soon_threadsafe(push)

        attached = manager.user_terminal_attach(session_id, deliver, terminal_id)
        if not attached.get("ok"):
            await ws.send_json({"type": "error", "data": {"error": attached.get("error", "")}})
            await ws.close(code=1011)
            return

        await ws.send_json(
            {"type": "terminal", "data": {**attached, "session_id": session_id, "terminal": terminal_id}}
        )
        delivered = int(attached.get("produced") or 0)

        async def sender() -> None:
            nonlocal delivered
            was_running = bool(attached.get("running"))
            while True:
                try:
                    chunk, start, end = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # A shell that exited between reads has no output left; saying so lets
                    # the panel stop pretending the prompt is live.
                    status = manager.user_terminal(session_id, terminal_id=terminal_id)
                    running = bool(status.get("running"))
                    if was_running and not running:
                        was_running = False
                        await ws.send_json(
                            {"type": "exit", "data": {"exit_code": status.get("exit_code")}}
                        )
                    continue
                if end <= delivered:
                    continue  # already covered by the attach snapshot
                if start < delivered:
                    chunk = chunk[delivered - start :]
                delivered = end
                await ws.send_json({"type": "output", "data": {"chunk": chunk}})

        send_task = asyncio.create_task(sender())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "input":
                    data = str(message.get("data", ""))
                    if len(data) > MAX_TERMINAL_INPUT_CHARS:
                        data = data[:MAX_TERMINAL_INPUT_CHARS]
                    manager.user_terminal_write(session_id, data, terminal_id)
                elif kind == "interrupt":
                    manager.user_terminal_interrupt(session_id, terminal_id)
                elif kind == "resize":
                    data = message.get("data") or {}
                    try:
                        cols = int(data.get("cols") or 0)
                        rows = int(data.get("rows") or 0)
                    except (TypeError, ValueError):
                        continue
                    if cols > 0 and rows > 0:
                        manager.user_terminal_resize(session_id, cols, rows, terminal_id)
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            manager.user_terminal_unsubscribe(session_id, deliver, terminal_id)
