"""Messaging tools that are listed by hand. Slack's come from its own spec (`slack_specs.py`)."""

from __future__ import annotations

from .types import ConnectorToolDef, row_makers

_read, _write = row_makers("whatsapp")
WHATSAPP: tuple[ConnectorToolDef, ...] = (
    _write("whatsapp_send_message", "Send message", "Send a WhatsApp text message.", target="to"),
    _write("whatsapp_send_template", "Send template", "Send an approved WhatsApp template message.", target="to"),
)

_read, _write = row_makers("teams")
TEAMS: tuple[ConnectorToolDef, ...] = (
    _read("teams_list_chats", "List chats", "List your chats with their last message."),
    _read("teams_read_chat", "Read chat", "Read a chat's recent messages."),
    _read("teams_list_teams", "List teams", "List the teams you are in."),
    _read("teams_list_channels", "List channels", "List a team's channels."),
    _read("teams_find_people", "Find people", "Find people in your organization."),
    _write("teams_send_chat", "Send chat message", "Send a chat message, or start a chat with people.", target="chat_id"),
    _write("teams_post_channel", "Post in channel", "Post in a channel, or reply in a thread.", target="channel_id"),
    _write("teams_react", "React", "React to a message, or take the reaction back."),
)

ALL: tuple[ConnectorToolDef, ...] = WHATSAPP + TEAMS
