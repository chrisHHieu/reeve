"""The gateway: owns the messaging adapters and routes what arrives.

It lives inside the always-on `reeve-server`, started and stopped with its lifespan. An inbound
message is checked against the platform's allow-list and then handed to the registered handler.
Replies go out through the `send_message` tool and not through the gateway, which stays a thin
inbound router.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Callable, Optional

from harness.safety.secrets import SecretStore

from ..config import ConnectorSettings, is_authorized, load_settings
from .adapter import BasePlatformAdapter
from .events import (
    MessageEvent,
    MessageHandler,
    SendResult,
    SessionSource,
    parse_target,
)

logger = logging.getLogger("harness.integrations.connectors")

#: How many distinct recent senders are kept, so a chat id can be captured for the allow-list.
_RECENT_LIMIT = 20


class Gateway:
    def __init__(
        self,
        *,
        secrets: Optional[SecretStore] = None,
        settings: Optional[dict[str, ConnectorSettings]] = None,
        handler: Optional[MessageHandler] = None,
        on_unauthorized: Optional[Callable] = None,
    ) -> None:
        self.secrets = secrets or SecretStore()
        self.settings = settings if settings is not None else load_settings(self.secrets)
        self._handler = handler
        # Awaited with the event when the allow-list drops a message, so it can be parked for a
        # one-step allow-and-deliver and not vanish.
        self._on_unauthorized = on_unauthorized
        self._adapters: dict[str, BasePlatformAdapter] = {}
        # Who wrote recently, for capturing chat ids. Identity only, never saved.
        self._recent: "OrderedDict[tuple[str, str], dict]" = OrderedDict()

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    def register(self, adapter: BasePlatformAdapter) -> None:
        adapter.set_message_handler(self._on_inbound)
        self._adapters[adapter.platform] = adapter

    def _authorized(self, platform: str, source: SessionSource) -> bool:
        settings = self.settings.get(platform)
        return settings is not None and is_authorized(settings, source)

    # -- inbound --------------------------------------------------------------------------

    async def _on_inbound(self, event: MessageEvent) -> None:
        self._remember(event)  # identity is captured even from a sender who is not allowed
        if not self._authorized(event.source.platform, event.source):
            logger.info("parking unauthorized inbound from %s", event.source.label())
            if self._on_unauthorized is not None:
                try:
                    await self._on_unauthorized(event)
                except Exception:
                    logger.exception("parking unauthorized inbound failed")
            return
        if self._handler is not None:
            await self._handler(event)

    def _remember(self, event: MessageEvent) -> None:
        source = event.source
        if not source.user_id:
            return
        key = (source.platform, source.user_id)
        self._recent.pop(key, None)  # re-inserting moves it to the newest end
        self._recent[key] = {
            "platform": source.platform,
            "user_id": source.user_id,
            "user_name": source.user_name,
            "chat_id": source.chat_id,
            "chat_type": source.chat_type,
            "target": source.target,
        }
        while len(self._recent) > _RECENT_LIMIT:
            self._recent.popitem(last=False)

    def recent_senders(self, platform: Optional[str] = None) -> list[dict]:
        """Who has written, newest first, for the allow-list screen."""
        return [row for row in reversed(self._recent.values()) if platform is None or row["platform"] == platform]

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> list[str]:
        """Connect every enabled adapter that is registered, and return the platforms that came
        up. A bad token or a network failure skips that platform and does not break the server."""
        live: list[str] = []
        for platform, settings in self.settings.items():
            adapter = self._adapters.get(platform)
            if not settings.enabled or adapter is None:
                continue
            try:
                if await adapter.connect():
                    live.append(platform)
            except Exception:
                logger.exception("failed to connect %s adapter", platform)
        return live

    async def stop(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.disconnect()
            except Exception:
                logger.exception("error disconnecting %s adapter", adapter.platform)

    # -- outbound --------------------------------------------------------------------------

    async def deliver(self, target: str, text: str) -> SendResult:
        """Send through a live adapter, for callers that prefer the persistent connection."""
        platform, chat_id, thread_id = parse_target(target)
        adapter = self._adapters.get(platform)
        if adapter is None:
            return SendResult(False, error=f"no adapter for {platform}")
        return await adapter.send(chat_id, text, thread_id=thread_id)

    def status(self) -> list[dict]:
        return [
            {
                "platform": platform,
                "enabled": settings.enabled,
                "connected": platform in self._adapters,
                "allow_all": settings.allow_all,
                "allowed_users": len(settings.allowed_users),
            }
            for platform, settings in self.settings.items()
        ]
