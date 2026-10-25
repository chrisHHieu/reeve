"""MentionsMixin: the Slack mention router, for a message that tags the bot in a channel."""

from __future__ import annotations

import logging
import re
import uuid

from harness.integrations.connectors import MessageSource
from harness.modes.agents import DEFAULT_AGENT
from harness.integrations.connectors.messaging import format_target

logger = logging.getLogger("harness.manager")

_MENTION_TOKEN = re.compile(r"<@[^>]+>")
_TITLE_ASK_CHARS = 48
_CONTEXT_LINES = 6


def _channel_label(source) -> str:
    return f"#{source.chat_name}" if source.chat_name else source.chat_id


class MentionsMixin:
    async def _route_mention(self, event, ms: MessageSource, subs) -> None:
        """The bot was tagged in a channel. A session the person subscribed to the channel owns
        it and must answer; otherwise a session per thread handles it, spawned on the
        first tag and steered by follow-ups (deduplicated on the thread target)."""
        source = event.source
        # Slack replies to a top-level message in a thread keyed on THAT message's timestamp, so
        # a top-level tag, which has no thread id, is keyed and answered on its own.
        thread_key = source.thread_id or getattr(event, "message_id", None)
        thread_target = format_target(source.platform, source.chat_id, thread_key)
        who = source.user_name or source.user_id or "?"
        channel = _channel_label(source)
        if subs:
            # The person connected a session to this channel, so it answers tags and nothing is
            # spawned.
            message = (
                f"🔔 You were tagged by {who} in {channel}: {event.text}\n"
                "(You are subscribed to this channel and were mentioned directly — you must "
                "respond. Reply in the thread with the send_message tool, target "
                f'"{thread_target}".)'
            )
            for sub in subs:
                if not self._inbound_connector_allowed(sub.session_id, source.platform):
                    continue
                try:
                    await self.deliver_to_session(sub.session_id, message, source=ms.to_dict())
                except Exception:
                    pass
            return
        owner = self.mention_sessions.get(thread_target)
        if owner and self.session_store.load(owner) is not None:
            # A follow-up tag in a thread already owned: steer the same session.
            message = (
                f"💬 Follow-up in your Slack thread ({channel}) from {who}: {event.text}\n"
                f'(Reply in the thread with the send_message tool, target "{thread_target}" '
                "— replies there are pre-approved.)"
            )
            await self.deliver_to_session(owner, message, source=ms.to_dict())
            return
        await self._spawn_mention_session(event, ms, thread_target)

    async def _spawn_mention_session(self, event, ms: MessageSource, thread_target: str) -> None:
        """The first tag in a thread: a NEW visible session that owns it. Its replies in
        the thread carry a standing grant (an exact target match), so the conversation never
        stalls on an approval nobody in Slack can see. Everything else asks as usual, and those
        approvals park in the Inbox."""
        source = event.source
        who = source.user_name or source.user_id or "?"
        channel = _channel_label(source)
        session_id = uuid.uuid4().hex
        mcp_tools = await self.arm_mcp(session_id, agent=DEFAULT_AGENT)
        engine = self.get_engine(session_id, agent=DEFAULT_AGENT, extra_tools=mcp_tools)
        if engine is None:
            self.unrouted.record(source.target, who, event.text, reason="could not spawn mention session")
            return
        # The durable mapping comes FIRST, so a quick follow-up tag mid-turn is deduplicated into
        # steering. Then the live grant, which `get_engine` re-derives from the store on any
        # rebuild.
        self.mention_sessions.set(thread_target, session_id, channel=f"{source.platform}:{source.chat_id}")
        engine.permissions.task_rules.setdefault("send_message", set()).add(thread_target)
        self.save(session_id, engine)  # the row must exist before the rename and `set_origin`
        # The title is the ASK first and the channel last. The text varies between sessions, so
        # it gets the truncation budget; the mention token is noise, since the origin is already
        # told by the "From Slack" group, the icon and the label.
        ask = " ".join(_MENTION_TOKEN.sub("", event.text or "").split())[:_TITLE_ASK_CHARS]
        self.session_store.rename(session_id, f"{ask} — {channel}" if ask else channel)
        self.session_store.set_origin(session_id, source.platform, channel)
        # Up to six lines of the channel's recent context, without the tag itself, which is the
        # opening line.
        recent = self.channel_buffer.recent(f"{source.platform}:{source.chat_id}", _CONTEXT_LINES + 1)[:-1]
        context = "\n".join(f"- {line['from']}: {line['text']}" for line in recent)
        opening = (
            f"🔔 You were mentioned on Slack in {channel} by {who}: {event.text}\n\n"
            "You own this Slack thread. Reply in the thread using the send_message tool "
            f'with target "{thread_target}" — replies to this thread are pre-approved and '
            "never prompt the user. Anything else (other channels, files, external "
            "actions) asks for approval as usual. Keep replies concise and "
            "Slack-appropriate." + (f"\n\nRecent channel context:\n{context}" if context else "")
        )
        try:
            await self.deliver_to_session(session_id, opening, source=ms.to_dict())
        except Exception:
            logger.exception("mention session %s opening turn failed", session_id)
