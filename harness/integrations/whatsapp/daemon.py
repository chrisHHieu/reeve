"""The one process that holds the WhatsApp connection.

A linked device keeps one connection: a second process connecting with the same session replaces
the first ("stream replaced"), so the connection cannot belong to a session. It lives here, beside
`reeve-server` and for as long as it does: new messages are kept while the app is open whether or
not a conversation is, and the store is the one place the read tools read.

It runs apart from the server because the protocol library is a Go runtime loaded into Python: a
fault in it kills its process (the first live run did, 2026-10-09), and that must not be the
server. It speaks plain JSON over HTTP on 127.0.0.1, behind a token written to `daemon.json` in the
account's folder, which only this user can read:

    GET  /status          linked? connected? the QR code while pairing (the Connectors page)
    POST /pair            {"phone"?} → the code to type on the phone
    POST /call            {"tool", "args", "workspace"} → a tool's result (the session servers)
    POST /logout          unlink this device from the phone, then exit

Each session's MCP server (`server.py`) forwards its calls here with the session's own folder, which
is how a file rule can know which folder is the caller's. The daemon exits when the process that
started it does (`--parent`), so a crashed server leaves no connection behind.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from .tools import Tools, account_folder

INFO_FILE = "daemon.json"
#: How often the daemon drops the messages older than the account's keep choice.
KEEP_EVERY_S = 3600.0
#: The tools a session server may call through `/call`, and nothing else.
TOOLS = (
    "status", "chats", "read", "search", "contacts", "download", "send", "react", "mark_read",
)
#: The tools that touch a session's files, which take its folder.
_WITH_WORKSPACE = ("download", "send")


def alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def dispatch(tools: Tools, tool: str, args: dict[str, Any], workspace: str) -> dict[str, Any]:
    """One tool call from a session server. An unknown tool or bad arguments is an error result."""
    if tool not in TOOLS:
        return {"error": f"unknown WhatsApp tool {tool!r}"}
    kwargs = dict(args or {})
    if tool in _WITH_WORKSPACE:
        kwargs["workspace"] = workspace
    try:
        return getattr(tools, tool)(**kwargs)
    except TypeError as exc:
        return {"error": f"bad arguments for {tool}: {exc}"}


def handler_for(token: str, routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # requests are not worth a console line each
            pass

        def _answer(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method: str) -> None:
            if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                return self._answer(401, {"error": "unauthorized"})
            route = routes.get(f"{method} {self.path}")
            if route is None:
                return self._answer(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
                self._answer(200, route(payload))
            except Exception as exc:  # a failing call is an answer, never a dropped socket
                self._answer(500, {"error": str(exc)})

        def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

    return Handler


def run(folder: Optional[Path] = None, parent: int = 0, nonce: str = "") -> int:
    from .link import Link

    folder = folder or account_folder()
    link = Link(folder)
    link.start()
    tools = Tools(link)
    token = secrets.token_urlsafe(32)
    done = threading.Event()

    def logout(_payload: dict[str, Any]) -> dict[str, Any]:
        result = link.logout()
        done.set()
        return result

    routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "GET /status": lambda _p: link.status(),
        "POST /pair": lambda p: {"code": link.pair_phone(str(p.get("phone") or ""))},
        "POST /call": lambda p: dispatch(tools, str(p.get("tool")), p.get("args") or {}, str(p.get("workspace") or "")),
        "POST /logout": logout,
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(token, routes))
    info = folder / INFO_FILE
    tmp = info.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": server.server_address[1], "token": token, "pid": os.getpid(), "nonce": nonce}), encoding="utf-8")
    os.replace(tmp, info)
    threading.Thread(target=server.serve_forever, name="whatsapp-daemon", daemon=True).start()
    link.apply_keep()
    kept_at = time.monotonic()
    try:
        while not done.is_set() and alive(parent):
            done.wait(2.0)
            if time.monotonic() - kept_at >= KEEP_EVERY_S:
                link.apply_keep()
                kept_at = time.monotonic()
    finally:
        server.shutdown()
        try:
            info.unlink()
        except OSError:
            pass
        link.stop()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parent = int(args[args.index("--parent") + 1]) if "--parent" in args else 0
    nonce = args[args.index("--nonce") + 1] if "--nonce" in args else ""
    return run(parent=parent, nonce=nonce)

