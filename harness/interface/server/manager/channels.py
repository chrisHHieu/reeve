"""Inbound messages from connected chat platforms: who gets them.

A channel message is buffered for catch-up and fanned out to the sessions subscribed to that
channel; a direct @-mention of the bot outranks that fan-out. A direct message goes to the session the
person designated for DMs, or is parked as unrouted so it stays visible.
"""

from __future__ import annotations

from harness.integrations.connectors import MessageSource

from .helpers import inbound_epoch

_CHANNEL_KINDS = ("channel", "group")


def _quiet_notice(name: str, who: str, text: str, channel: str) -> str:
    """The framing for channel traffic that did not mention the bot: judgement only, silence is the
    default. The must-respond framing belongs to the mention path."""
    return (
        f"💬 New message on {name} from {who}: {text}\n"
        f"(You're subscribed to this channel but were NOT mentioned. Use your "
        f"judgement: stay silent unless the message clearly concerns your job and "
        f"a reply adds real value — most channel chatter needs no response from "
        f'you. If you do reply, use the send_message tool with target "{channel}".)'
    )


class ChannelsMixin:
    def _message_source(self, event) -> MessageSource:
        """The structured, display-only record of where a message came from, built from the identities
        already resolved on the event. The framed text sent to the model stays its own `content`;
        `text` here is the raw message."""
        source = event.source
        return MessageSource(
            connector=source.platform,
            kind="channel" if source.chat_type in _CHANNEL_KINDS else "dm",
            channel_id=source.chat_id,
            channel_name=source.chat_name or source.chat_id,
            sender_id=source.user_id or "",
            sender_name=source.user_name or source.user_id or "?",
            ts=inbound_epoch(getattr(event, "message_id", None)),
            text=getattr(event, "text", "") or "",
        )

    async def _dispatch_inbound(self, event) -> None:
        """Route one inbound message that is not a token reply."""
        source = event.source
        self._note_person(source.platform, source.user_id, source.user_name)
        described = self._message_source(event)
        if source.chat_type in _CHANNEL_KINDS:
            await self._dispatch_channel_message(event, described)
        else:
            await self._dispatch_direct_message(event, described)

    async def _dispatch_channel_message(self, event, described: MessageSource) -> None:
        source = event.source
        text = described.text
        who = source.user_name or source.user_id or "?"
        channel = f"{source.platform}:{source.chat_id}"  # the channel address, without a thread
        self.channel_buffer.record(channel, who, text, name=source.chat_name)  # buffered even when unsubscribed
        subscribers = self.subscriptions.for_channel(channel)
        if getattr(event, "mentions_me", False):
            # A direct mention outranks the passive fan-out: subscribed sessions must answer it, and
            # an unsubscribed channel starts (or steers) the per-thread session.
            await self._route_mention(event, described, subscribers)
            return
        if not subscribers:
            return  # nobody is listening to this channel
        notice = _quiet_notice(source.chat_name or channel, who, text, channel)
        for subscription in subscribers:
            # A session that muted this connector skips delivery; the message is still in the buffer
            # for catch-up.
            if not self._inbound_connector_allowed(subscription.session_id, source.platform):
                continue
            try:
                await self.deliver_to_session(subscription.session_id, notice, source=described.to_dict())
            except Exception:
                pass

    async def _dispatch_direct_message(self, event, described: MessageSource) -> None:
        source = event.source
        who = source.user_name or source.user_id or "?"
        session_id = self.dm_session()
        if session_id and self._inbound_connector_allowed(session_id, source.platform):
            await self.deliver_to_session(session_id, event.tagged_text(), source=described.to_dict())
            return
        # Parked rather than delivered, with the reason, so it stays visible.
        reason = "connector muted for DM session" if session_id else "no DM session designated"
        self.unrouted.record(source.target, who, described.text, reason=reason)
