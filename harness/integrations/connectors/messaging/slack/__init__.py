"""The Slack adapter: Socket Mode in, stateless senders out."""

from __future__ import annotations

from .adapter import SlackAdapter
from .events import slack_event_to_event

__all__ = ["SlackAdapter", "slack_event_to_event"]
