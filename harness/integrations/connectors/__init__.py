"""Connectors: the outside services an agent can be given, their tools, and the messaging platforms
people reach it through."""

from __future__ import annotations

from .catalog import connector_for_tool
from .config import ConnectorSettings, is_authorized, load_settings
from .descriptors import ConnectorDescriptor, get_descriptor, list_descriptors
from .integration_tools import make_integration_tools
from .lifecycle import connect_connector, connector_list, disconnect_connector, experimental_enabled, set_experimental_enabled, update_connector_tools
from .messaging import (
    DEFAULT_SENDERS,
    BasePlatformAdapter,
    FakeAdapter,
    MessageEvent,
    MessageSource,
    MessageType,
    SendResult,
    SessionSource,
    SlackAdapter,
    TelegramAdapter,
    format_target,
    make_adapter,
    parse_target,
    slack_event_to_event,
    telegram_message_to_event,
)
from .messaging.gateway import Gateway
from .outbound import make_send_file_tool, make_send_message_tool

__all__ = sorted(
    [
        # messaging
        "BasePlatformAdapter",
        "DEFAULT_SENDERS",
        "FakeAdapter",
        "Gateway",
        "MessageEvent",
        "MessageSource",
        "MessageType",
        "SendResult",
        "SessionSource",
        "SlackAdapter",
        "TelegramAdapter",
        "format_target",
        "make_adapter",
        "parse_target",
        "slack_event_to_event",
        "telegram_message_to_event",
        # settings and the catalog
        "ConnectorDescriptor",
        "ConnectorSettings",
        "connector_for_tool",
        "get_descriptor",
        "is_authorized",
        "list_descriptors",
        "load_settings",
        # connecting
        "connect_connector",
        "connector_list",
        "disconnect_connector",
        "experimental_enabled",
        "set_experimental_enabled",
        "update_connector_tools",
        # tools
        "make_integration_tools",
        "make_send_file_tool",
        "make_send_message_tool",
    ]
)
