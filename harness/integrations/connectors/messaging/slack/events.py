"""Reading a raw Slack event: which messages count, and how an @-mention is written."""

from __future__ import annotations

import re
from typing import Optional

from ..events import MessageEvent, SessionSource

# Slack writes an @-mention in message text as `<@U0123>` (older form `<@U0123|name>`): a token, not
# the display name. It is resolved when the message is taken in, so every place that shows it (parked
# cards, transcripts, the channel buffer) shows "@name" and not the raw id.
MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")


def mention_pattern(user_id: str) -> "re.Pattern[str]":
    """The token for one user id, in either spelling."""
    return re.compile(rf"<@{re.escape(user_id)}(?:\|[^>]*)?>")


def slack_event_to_event(event: dict, bot_user_id: Optional[str]) -> Optional[MessageEvent]:
    """A Slack message event as a `MessageEvent`, or None for one the bot should not answer: its own
    echoes, edits, joins and other subtyped events, and empty text."""
    if event.get("bot_id") or event.get("subtype"):
        return None
    if bot_user_id and event.get("user") == bot_user_id:
        return None
    text = event.get("text") or ""
    if not text:
        return None
    source = SessionSource(
        platform="slack",
        chat_id=str(event.get("channel", "")),
        user_id=event.get("user"),
        chat_type="dm" if event.get("channel_type") == "im" else "channel",
        thread_id=event.get("thread_ts"),
    )
    # Whether the bot was mentioned is read from the raw text, tokens included; callers rewrite the
    # mentions to display names only after this mapping.
    mentions_me = bool(bot_user_id and mention_pattern(bot_user_id).search(text))
    return MessageEvent(text=text, source=source, message_id=event.get("ts"), mentions_me=mentions_me)
