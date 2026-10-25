"""An in-memory platform for tests and the `cli fake` REPL: inbound messages are injected by
hand and what was sent can be inspected, so the gateway and the handler loop run end to end with
no network and no real tokens."""

from __future__ import annotations

from typing import Optional

from .adapter import BasePlatformAdapter
from .events import MessageEvent, SendResult, SessionSource


class FakeAdapter(BasePlatformAdapter):
    platform = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.connected = False
        self.outbox: list[dict] = []  # {chat_id, text, thread_id}

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def send(self, chat_id: str, text: str, *, thread_id: Optional[str] = None) -> SendResult:
        self.outbox.append({"chat_id": chat_id, "text": text, "thread_id": thread_id})
        return SendResult(True, message_id=str(len(self.outbox)))

    async def inject(
        self,
        text: str,
        *,
        chat_id: str = "c1",
        user_id: str = "u1",
        user_name: str = "tester",
        chat_type: str = "dm",
        thread_id: Optional[str] = None,
    ) -> None:
        """Pretend a message arrived from the platform."""
        source = SessionSource(
            platform=self.platform, chat_id=chat_id, user_id=user_id, user_name=user_name,
            chat_type=chat_type, thread_id=thread_id,
        )
        await self.handle_message(MessageEvent(text=text, source=source, message_id=f"m{user_id}"))
