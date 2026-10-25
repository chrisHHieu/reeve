"""Building the adapter for a connected platform."""

from __future__ import annotations

from typing import Optional

from .adapter import BasePlatformAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter


def make_adapter(platform: str, profile: dict, *, secrets=None) -> Optional[BasePlatformAdapter]:
    """The adapter for a platform, built from its stored profile, or None when the profile is not
    complete.

    Telegram long-polls. Slack uses Socket Mode with a bot token and an app token, for one
    workspace. GitHub has no inbound adapter: its only one was a cloud relay, and there is no
    cloud any more. A webhook needs a public URL to be delivered to, and a desktop has none.
    """
    if platform == "telegram" and profile.get("bot_token"):
        return TelegramAdapter(profile["bot_token"])
    if platform == "slack" and profile.get("bot_token") and profile.get("app_token"):
        return SlackAdapter(profile["bot_token"], profile["app_token"])
    return None
