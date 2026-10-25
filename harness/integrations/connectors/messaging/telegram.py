"""Telegram: receive by long polling, send through the stateless sender.

The SDK is imported inside `connect()`, so this module loads without it; it is an optional extra.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .adapter import BasePlatformAdapter
from .events import MessageEvent, SendResult, SessionSource
from .senders import send_telegram

logger = logging.getLogger("harness.integrations.connectors")


def telegram_message_to_event(msg: Any) -> Optional[MessageEvent]:
    """A Telegram message as a `MessageEvent`, or None when it has no text."""
    text = getattr(msg, "text", None)
    if not text:
        return None
    chat = msg.chat
    user = getattr(msg, "from_user", None)
    private = str(getattr(chat, "type", "private")).lower().endswith("private")
    thread = getattr(msg, "message_thread_id", None)
    source = SessionSource(
        platform="telegram",
        chat_id=str(chat.id),
        user_id=str(user.id) if user else None,
        user_name=getattr(user, "full_name", None) if user else None,
        chat_type="dm" if private else "group",
        thread_id=str(thread) if thread else None,
    )
    return MessageEvent(text=text, source=source, message_id=str(getattr(msg, "message_id", "")))


class TelegramAdapter(BasePlatformAdapter):
    platform = "telegram"

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self._app = None

    async def connect(self) -> bool:
        try:
            from telegram.ext import Application, MessageHandler, filters
        except ImportError:
            logger.warning("python-telegram-bot not installed — `pip install reeve[messaging]`")
            return False

        self._app = Application.builder().token(self.token).build()

        async def on_update(update, _context):
            event = telegram_message_to_event(update.effective_message)
            if event is not None:
                await self.handle_message(event)

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_update))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram adapter polling")
        return True

    async def disconnect(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None

    async def send(self, chat_id: str, text: str, *, thread_id: Optional[str] = None) -> SendResult:
        return send_telegram(self.token, chat_id, text, thread_id)
