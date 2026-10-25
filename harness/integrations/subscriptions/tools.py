"""The tools a messaging persona uses to listen to channels."""

from __future__ import annotations

from typing import Optional

from .buffer import ChannelBuffer
from .channels import resolve_channel
from .records import SubscriptionStore


def subscription_tools(
    store: SubscriptionStore,
    session_id: str,
    buffer: ChannelBuffer,
    *,
    default_platform: str = "slack",
) -> list:
    """Subscribe, unsubscribe, list and catch up. The agent gets a channel by asking the person
    (ask_user) or from the message it is reacting to."""

    def address(channel: str) -> str:
        return resolve_channel(channel, default_platform=default_platform)

    def subscribe_channel(channel: str) -> dict:
        """Subscribe THIS session to a messaging channel so you receive its messages (a steer while
        you work, or a fresh turn when idle). Ask the user which channel (ask_user) if you don't
        already have one. `channel` may be a Slack `#channel` mention, a `platform:chat_id` address,
        or a channel id."""
        addr = address(channel)
        if not addr or ":" not in addr:
            return {"ok": False, "error": f"could not resolve a channel from {channel!r}"}
        store.subscribe(session_id, addr)
        return {"ok": True, "subscribed": addr}

    def unsubscribe_channel(channel: str) -> dict:
        """Stop THIS session from listening to a channel."""
        addr = address(channel)
        return {"ok": True, "unsubscribed": addr, "was_subscribed": store.unsubscribe(session_id, addr)}

    def list_subscriptions() -> dict:
        """List the channels THIS session is subscribed to."""
        return {"channels": [sub.channel for sub in store.for_session(session_id)]}

    def get_channel_messages(channel: str, n: int = 10) -> dict:
        """Get the last `n` messages seen on a channel (to catch up on anything you might have
        missed). Only messages received while the server was running are available."""
        addr = address(channel)
        return {"channel": addr, "messages": buffer.recent(addr, n)}

    return [subscribe_channel, unsubscribe_channel, list_subscriptions, get_channel_messages]
