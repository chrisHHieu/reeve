"""Display names for the ids an event carries, looked up once and kept."""

from __future__ import annotations

from typing import Any, Optional

from .events import MENTION, mention_pattern


class NameBook:
    """Users and channels by id. An event carries only ids; names are looked up so senders and the
    allow-list do not read "unknown", and a card shows "#general" and not "C...".

    `client` is a callable returning the live Slack Web API client, which does not exist before the
    adapter connects.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._users: dict[str, str] = {}  # user id -> display name
        self._channels: dict[str, str] = {}  # chat id -> channel name

    async def user(self, user_id: Optional[str]) -> Optional[str]:
        """A user's display name through `users.info`, kept after the first lookup. None when it
        cannot be found, and the caller then falls back to the id."""
        if not user_id:
            return None
        if user_id in self._users:
            return self._users[user_id]
        try:
            found = (await self._client().users_info(user=user_id)).get("user") or {}
            profile = found.get("profile") or {}
            name = profile.get("display_name") or profile.get("real_name") or found.get("real_name") or found.get("name")
        except Exception:
            name = None
        if name:
            self._users[user_id] = name
        return name

    async def channel(self, chat_id: Optional[str]) -> Optional[str]:
        """A channel's or DM's name through `conversations.info`, kept after the first lookup."""
        if not chat_id:
            return None
        if chat_id in self._channels:
            return self._channels[chat_id]
        try:
            found: Any = (await self._client().conversations_info(channel=chat_id)).get("channel") or {}
            name = found.get("name") or found.get("name_normalized")
        except Exception:
            name = None
        if name:
            self._channels[chat_id] = name
        return name

    async def resolve_mentions(self, text: str) -> str:
        """`<@U...>` tokens rewritten as `@display-name`. An id that does not resolve (a missing
        scope, a deleted user) keeps its token."""
        for user_id in set(MENTION.findall(text or "")):
            name = await self.user(user_id)
            if name:
                text = mention_pattern(user_id).sub(f"@{name}", text)
        return text
