"""FakeSlack: the part of Slack the gateway talks to, served from inside the test process.

The real `SlackAdapter` and slack_bolt run against it unchanged. Slack's SDK takes its base URL
from `SLACK_API_URL`, and the socket address is itself an answer from the Web API
(`apps.connections.open`), so pointing that one variable here moves both the HTTP calls and the
Socket Mode connection. Nothing reaches the network and any token will do.

A test drives it by calling the methods below; curl and the standalone runner use the same verbs
through `/control/*` (`control.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .control import control_routes
from .envelopes import BOT_USER_ID, button_envelope, hello_frame, message_envelope
from .web_api import Clock, WebApi, read_params

logger = logging.getLogger("harness.testing.fake_slack")


class _Sockets:
    """The Socket Mode clients connected now, and how many times one has connected at all."""

    def __init__(self) -> None:
        self.open: set[WebSocket] = set()
        self.connects = 0
        self.greeted = asyncio.Event()
        self.replies: list[dict] = []  # what clients sent back: acks of the envelopes

    async def serve(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(hello_frame())
        self.open.add(websocket)
        self.connects += 1
        self.greeted.set()
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    self.replies.append(json.loads(text))
                except ValueError:
                    continue
        except WebSocketDisconnect:
            return
        except Exception:
            logger.debug("FakeSlack socket closed", exc_info=True)
        finally:
            self.open.discard(websocket)

    async def send(self, envelope: dict) -> None:
        """To every client; one that cannot take it is taken off the list."""
        text = json.dumps(envelope)
        for websocket in list(self.open):
            try:
                await websocket.send_text(text)
            except Exception:
                self.open.discard(websocket)

    async def close_all(self) -> None:
        for websocket in list(self.open):
            try:
                await websocket.close()
            except Exception:
                logger.debug("FakeSlack socket would not close", exc_info=True)
        self.open.clear()


class FakeSlack:
    """`await start()`, export `api_url` as `SLACK_API_URL`, play a scenario, read `outbound()`."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port  # 0 takes any free port; start() puts the real one here
        self.bot_user_id = BOT_USER_ID
        self._clock = Clock()
        self._api = WebApi(self._clock, self.bot_user_id, lambda: self.ws_url)
        self._sockets = _Sockets()
        self._server: Optional[uvicorn.Server] = None
        self._serving: Optional[asyncio.Task] = None
        self.app = Starlette(
            routes=[
                Route("/api/{method}", self._answer_web_api, methods=["GET", "POST"]),
                WebSocketRoute("/socket", self._sockets.serve),
                *control_routes(self),
            ]
        )

    # -- addresses --------------------------------------------------------------------------

    @property
    def api_url(self) -> str:
        """The value for `SLACK_API_URL`; the SDK wants the trailing slash."""
        return f"http://{self.host}:{self.port}/api/"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/socket"

    @property
    def control_url(self) -> str:
        return f"http://{self.host}:{self.port}/control"

    # -- what happened ----------------------------------------------------------------------

    @property
    def users(self) -> dict[str, dict]:
        return self._api.users

    @property
    def channels(self) -> dict[str, dict]:
        return self._api.channels

    @property
    def api_calls(self) -> list[str]:
        """Every Web API method called, in order."""
        return self._api.calls

    @property
    def unknown_methods(self) -> list[str]:
        """Methods the fake has no answer for: a sign the adapter started using something new."""
        return self._api.unknown_methods

    def outbound(self) -> list[dict]:
        """What the app posted or edited (`chat.postMessage`, `chat.update`), oldest first."""
        return list(self._api.sent)

    def acks(self) -> list[dict]:
        return list(self._sockets.replies)

    @property
    def live_sockets(self) -> int:
        return len(self._sockets.open)

    @property
    def socket_connections(self) -> int:
        """How many times a client has connected; each reconnect counts."""
        return self._sockets.connects

    # -- running ----------------------------------------------------------------------------

    async def start(self) -> "FakeSlack":
        self._server = uvicorn.Server(
            uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", lifespan="off", access_log=False)
        )
        self._serving = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        await self._sockets.close_all()
        server, serving = self._server, self._serving
        self._server = self._serving = None
        if server is not None:
            server.should_exit = True
        if serving is not None:
            try:
                await asyncio.wait_for(serving, timeout=5)
            except Exception:
                serving.cancel()

    async def __aenter__(self) -> "FakeSlack":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # -- playing a scenario -----------------------------------------------------------------

    def add_user(self, id: str, name: str, real_name: Optional[str] = None, display_name: Optional[str] = None) -> None:
        self._api.add_user(id, name, real_name, display_name)

    def add_channel(self, id: str, name: str, is_im: bool = False) -> None:
        self._api.add_channel(id, name, is_im)

    async def inbound(
        self, channel: str, user: str, text: str, thread_ts: Optional[str] = None, channel_type: Optional[str] = None
    ) -> str:
        """A person writes `text` in `channel`; every connected client gets the event. The message's
        `ts` is returned. A channel added with `is_im` arrives as a DM unless `channel_type` says."""
        if channel_type is None:
            channel_type = "im" if (self.channels.get(channel) or {}).get("is_im") else "channel"
        ts = self._clock.next()
        await self._sockets.send(
            message_envelope(
                channel=channel, channel_type=channel_type, user=user, text=text, ts=ts, thread_ts=thread_ts, bot_user_id=self.bot_user_id
            )
        )
        return ts

    async def interaction(self, channel: str, user: str, username: str, message_ts: str, action_id: str, value: str) -> None:
        """A person presses a button on the message at `message_ts`."""
        await self._sockets.send(
            button_envelope(
                channel=channel,
                channel_name=(self.channels.get(channel) or {}).get("name", "channel"),
                user=user,
                username=username,
                message_ts=message_ts,
                action_id=action_id,
                value=value,
                action_ts=self._clock.next(),
                response_url_base=self.api_url,
                bot_user_id=self.bot_user_id,
            )
        )

    async def reset(self) -> None:
        """Forget the directory and every record, and drop the clients."""
        self._api.clear()
        self._sockets.replies.clear()
        await self._sockets.close_all()

    # -- the socket -------------------------------------------------------------------------

    async def wait_socket(self, timeout: float = 5.0) -> None:
        """Until a client is connected and has had its `hello`."""
        await asyncio.wait_for(self._sockets.greeted.wait(), timeout=timeout)

    async def wait_socket_connections(self, at_least: int, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        give_up = loop.time() + timeout
        while self._sockets.connects < at_least:
            if loop.time() > give_up:
                raise asyncio.TimeoutError(f"{self._sockets.connects} socket connects, wanted {at_least}")
            await asyncio.sleep(0.02)

    async def close_sockets(self) -> None:
        """Close every connection from this side, the way Slack recycles one; a client that wants
        events has to connect again."""
        await self._sockets.close_all()
        self._sockets.greeted.clear()

    async def _answer_web_api(self, request: Request) -> JSONResponse:
        return JSONResponse(self._api.answer(request.path_params["method"], await read_params(request)))
