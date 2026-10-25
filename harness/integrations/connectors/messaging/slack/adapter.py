"""Slack: receive over Socket Mode, send through the stateless senders.

The SDKs are imported inside `connect()`, so this package loads without them; they are an optional
extra. The mapping from a raw event to a `MessageEvent` is a pure function in `events.py`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from ..adapter import BasePlatformAdapter
from ..events import SendResult
from ..senders import send_slack
from .events import slack_event_to_event
from .names import NameBook
from .watchdog import watch

logger = logging.getLogger("harness.integrations.connectors")


class SlackAdapter(BasePlatformAdapter):
    platform = "slack"

    #: How often the watchdog checks the live connection (tests shorten it).
    _WATCHDOG_INTERVAL = 20.0

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        *,
        watchdog_interval: Optional[float] = None,
        auto_reconnect: bool = True,
    ) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.app_token = app_token
        self._app = None
        self._socket = None
        self._task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._closing = False
        self._reconnects = 0  # how many times the watchdog has revived the connection
        self._watchdog_interval = self._WATCHDOG_INTERVAL if watchdog_interval is None else watchdog_interval
        # The SDK's own reconnect stays on in production, where it is seamless when Slack cycles
        # connections. Tests turn it off so the watchdog is the only, deterministic way back.
        self._auto_reconnect = auto_reconnect
        self._bot_user_id: Optional[str] = None
        self._book = NameBook(lambda: self._app.client)

    def _count_reconnect(self) -> int:
        self._reconnects += 1
        return self._reconnects

    async def _on_message(self, event: dict) -> None:
        mapped = slack_event_to_event(event, self._bot_user_id)
        if mapped is None:
            return
        source = mapped.source
        if not source.user_name:
            source.user_name = await self._book.user(source.user_id)
        if not source.chat_name:
            source.chat_name = await self._book.channel(source.chat_id)
        mapped.text = await self._book.resolve_mentions(mapped.text)
        await self.handle_message(mapped)

    async def connect(self) -> bool:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError:
            logger.warning("slack-bolt not installed — `pip install reeve[messaging]`")
            return False

        # The base URL can be overridden so tests (and the fake Slack) redirect every Web API call,
        # Socket Mode's `apps.connections.open` included, which the handler makes on this client.
        client = AsyncWebClient(token=self.bot_token, base_url=os.environ.get("SLACK_API_URL", "https://slack.com/api/"))
        self._app = AsyncApp(client=client)
        try:
            self._bot_user_id = (await self._app.client.auth_test()).get("user_id")
        except Exception:
            logger.exception("slack auth_test failed")
            return False

        @self._app.event("message")
        async def on_message(event, _say):
            await self._on_message(event)

        self._closing = False
        self._socket = AsyncSocketModeHandler(self._app, self.app_token)
        self._socket.client.auto_reconnect_enabled = self._auto_reconnect
        self._task = asyncio.create_task(self._socket.start_async())
        self._watchdog_task = asyncio.create_task(
            watch(lambda: self._socket, lambda: self._closing, self._watchdog_interval, self._count_reconnect)
        )
        logger.info("slack adapter connected (socket mode) as %s", self._bot_user_id)
        return True

    async def resolve_user_name(self, user_id: Optional[str]) -> Optional[str]:
        return await self._book.user(user_id)

    async def resolve_channel_name(self, chat_id: Optional[str]) -> Optional[str]:
        return await self._book.channel(chat_id)

    async def disconnect(self) -> None:
        self._closing = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._socket is not None:
            try:
                await self._socket.close_async()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def send(self, chat_id: str, text: str, *, thread_id: Optional[str] = None) -> SendResult:
        # The stateless senders block on httpx, so they run in a thread: an outbound sent from the
        # event loop must not stall the server on the Slack round trip.
        return await asyncio.to_thread(send_slack, self.bot_token, chat_id, text, thread_id)
