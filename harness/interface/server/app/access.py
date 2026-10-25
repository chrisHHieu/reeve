"""Who may talk to the local server: the sidecar token and the browser Origin.

The server binds to 127.0.0.1, yet a page in the person's own browser can still reach
loopback. Without an origin gate any website they visit could read the session list and, over
the WebSocket (which CORS never covers), drive a session into shell and file tools. So browser
origins are pinned to the desktop app (`reeve://app`; the Tauri origins until its installs are
replaced) and to localhost. A request with no Origin at all
(curl, native clients, tests) passes: only browsers attach one, and they cannot forge it.
"""

from __future__ import annotations

import re
import secrets
from typing import Callable, Optional

from fastapi import Request, WebSocket

ORIGIN_PATTERN = re.compile(
    r"^(reeve://app"  # the desktop app's own scheme (Electron)
    r"|tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://tauri\.localhost)$"
)

# The WebSocket subprotocol that carries the token (browsers cannot set headers on a socket).
SUBPROTOCOL = "reeve"
TOKEN_HEADER = "x-reeve-token"
POLICY_VIOLATION = 1008


def origin_allowed(origin: Optional[str]) -> bool:
    return origin is None or bool(ORIGIN_PATTERN.match(origin))


class Gate:
    """The token this server was started with ("" means no token is required)."""

    def __init__(self, token: str) -> None:
        self.token = token
        #: The preview door's origin once it listens, read through this callable.
        #: A page served there is one the session wrote. It never reaches this API: with a
        #: token it has none to send, and without one (a dev server) this is the only bar.
        self.never: Callable[[], Optional[str]] = lambda: None

    def refused_origin(self, origin: Optional[str]) -> bool:
        return bool(origin) and origin == self.never()

    def request_ok(self, request: Request) -> bool:
        provided = request.headers.get(TOKEN_HEADER, "")
        return bool(self.token and provided and secrets.compare_digest(provided, self.token))

    def socket_ok(self, ws: WebSocket) -> bool:
        if not self.token:
            return True
        offered = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        return any(secrets.compare_digest(part, self.token) for part in offered)

    async def admit(self, ws: WebSocket) -> bool:
        """Accept the socket if its token and Origin are fine; otherwise close it as a policy
        violation. CORS never gates a WebSocket, hence the Origin check here."""
        origin = ws.headers.get("origin")
        if not self.socket_ok(ws) or not origin_allowed(origin) or self.refused_origin(origin):
            await ws.close(code=POLICY_VIOLATION)
            return False
        await ws.accept(subprotocol=SUBPROTOCOL if self.token else None)
        return True
