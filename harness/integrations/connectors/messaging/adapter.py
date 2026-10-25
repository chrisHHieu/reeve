"""The contract every messaging platform implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .events import (
    MessageEvent,
    MessageHandler,
    SendResult,
)


class BasePlatformAdapter(ABC):
    """One messaging platform. A subclass connects and listens, sends, and passes what it
    receives to `handle_message`."""

    platform: str = "base"

    def __init__(self) -> None:
        self._handler: Optional[MessageHandler] = None

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    @abstractmethod
    async def connect(self) -> bool:
        """Connect and start listening. True when it worked."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop listening and close the connection."""

    @abstractmethod
    async def send(self, chat_id: str, text: str, *, thread_id: Optional[str] = None) -> SendResult:
        """Send a message."""

    async def handle_message(self, event: MessageEvent) -> None:
        if self._handler is not None:
            await self._handler(event)
