"""Working out where a message goes: the target token, a Slack channel named in words, and the
token to send with."""

from __future__ import annotations

import re
from typing import Optional

from harness.safety.secrets import SecretStore

from ..messaging import parse_target

# Slack channel names are strictly lowercase (letters, digits, `-`, `.`, `_`), and ids are
# uppercase tokens that start with C, D, G or U. Anything lowercase, or written with a `#`, is a
# name someone said; everything else is taken as an address.
_SLACK_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def looks_like_slack_name(chat_id: str) -> bool:
    return chat_id.startswith("#") or bool(_SLACK_NAME.match(chat_id))


def parse_or_coerce(target: str) -> tuple[str, str, Optional[str]]:
    """`parse_target`, except that a bare channel name (`all-reeve`, `#general`) becomes a Slack
    target. Models pass what the person said, and a lowercase or `#` name is Slack-shaped; one
    never wrote the `slack:` prefix on its own. Telegram targets are numeric, so the two shapes do
    not collide."""
    try:
        platform, chat_id, thread_id = parse_target(target)
    except ValueError:
        raw = (target or "").strip()
        if raw and looks_like_slack_name(raw.lstrip("#")):
            return "slack", raw, None
        raise
    if platform == "slack" and "/" in chat_id:
        # A target saved while the hosted relay existed names its workspace (`T…/C…`). There is
        # one workspace now, and a stored automation or standing rule has to keep working.
        chat_id = chat_id.split("/", 1)[1]
    return platform, chat_id, thread_id


def resolve_slack_channel(secrets: SecretStore, name: str) -> tuple[Optional[str], Optional[str]]:
    """(channel id, None) for a channel named in words, or (None, why).

    The name is looked up in the cached channel list the app's channel picker uses. Exactly one
    match is used; none returns an error that says what to do and never a guess, so "post Hi to
    <channel>" just works when Slack is connected.
    """
    from ..slack_directory import list_channels

    query = name.lstrip("#").strip()
    if not (secrets.get("slack:default") or {}).get("bot_token"):
        return None, "no bot token for slack — connect it first"
    listing = list_channels(secrets, query, limit=50)
    matches = [
        channel
        for channel in (listing.get("channels") or [])
        if listing.get("ok") and str(channel.get("name", "")).lower() == query.lower()
    ]
    if not matches:
        return None, (
            f"no Slack channel named #{query} in the connected workspace — check the "
            "name, or pass the channel id (slack:C…)"
        )
    channel = matches[0]
    if not channel.get("is_member"):
        return None, f"found #{query}, but the bot isn't a member — invite @Reeve to #{query} in Slack, then retry"
    return str(channel["id"]), None


def bot_token(secrets: SecretStore, platform: str) -> Optional[str]:
    """The bot token to send with, from `<platform>:default`."""
    return (secrets.get(f"{platform}:default") or {}).get("bot_token")
