"""Messaging platforms: Telegram and Slack in, the gateway that routes what arrives, and the
one-shot senders that reply.

`Gateway` is not imported here: it needs `connectors.config`, which needs the types in this
package, so it is imported from `.gateway` where it is used.
"""

from .adapter import BasePlatformAdapter
from .events import (
    MessageEvent,
    MessageHandler,
    MessageSource,
    MessageType,
    SendResult,
    SessionSource,
    format_target,
    parse_target,
)
from .factory import make_adapter
from .fake import FakeAdapter
from .parked import ParkedMessage, ParkedStore
from .senders import DEFAULT_FILE_SENDERS, DEFAULT_SENDERS, FileSender, Sender
from .slack import SlackAdapter, slack_event_to_event
from .telegram import TelegramAdapter, telegram_message_to_event

__all__ = [
    "BasePlatformAdapter",
    "DEFAULT_FILE_SENDERS",
    "DEFAULT_SENDERS",
    "FakeAdapter",
    "FileSender",
    "MessageEvent",
    "MessageHandler",
    "MessageSource",
    "MessageType",
    "ParkedMessage",
    "ParkedStore",
    "SendResult",
    "Sender",
    "SessionSource",
    "SlackAdapter",
    "TelegramAdapter",
    "format_target",
    "make_adapter",
    "parse_target",
    "slack_event_to_event",
    "telegram_message_to_event",
]
