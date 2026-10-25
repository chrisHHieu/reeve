"""Connectors people talk through: Telegram, Slack, IMAP email and WhatsApp."""

from __future__ import annotations

from ..descriptor.model import ConnectorDescriptor, Field
from ..descriptor.oauth_clients import ALLOWED_USERS_FIELD
from ..descriptor.validators import validate_email, validate_slack, validate_telegram, validate_whatsapp

_SLACK_BOT_SCOPES = (
    "chat:write, files:write, app_mentions:read, im:history, channels:history, groups:history, "
    "users:read, channels:read, groups:read"
)
_SLACK_TOOL_SCOPES = (
    "channels:join, chat:write.public, im:read, im:write, mpim:read, mpim:history, mpim:write, "
    "users:read.email, usergroups:read, files:read, reactions:read, reactions:write, pins:read, "
    "pins:write, bookmarks:read, bookmarks:write, canvases:read, canvases:write, dnd:read, "
    "assistant:write"
)


def build() -> list[ConnectorDescriptor]:
    telegram = ConnectorDescriptor(
        name="telegram",
        title="Telegram",
        icon="✈",
        blurb="Talk both ways with a Telegram bot.",
        auth="bot_token",
        two_way=True,
        channels=True,
        brand_color="#229ed9",
        logo="telegram",
        fields=[
            Field("bot_token", "Bot token", secret=True, help="Given to you by @BotFather.", placeholder="123456:ABC-DEF…"),
            ALLOWED_USERS_FIELD,
        ],
        instructions=[
            "Open Telegram and start a chat with @BotFather.",
            "Send /newbot and choose a name and a username.",
            "Copy the HTTP API token it replies with and paste it below.",
            "Once connected, message your new bot one time, then press Capture to pick up your user ID.",
        ],
        validate=validate_telegram,
    )
    slack = ConnectorDescriptor(
        name="slack",
        title="Slack",
        icon="💬",
        blurb="Two-way messaging plus the Slack toolbox: read, post, search, schedule, canvases and "
        "channels. It connects through a Slack app of your own (Socket Mode).",
        auth="socket_app",
        two_way=True,
        channels=True,
        brand_color="#611f69",
        logo="slack",
        # A Slack app of the person's own: a bot token, a Socket Mode app token, and optionally
        # a user token.
        fields=[
            Field("bot_token", "Bot token", secret=True, help="The Bot User OAuth Token.", placeholder="xoxb-…"),
            Field("app_token", "App token", secret=True, help="The app-level token for Socket Mode.", placeholder="xapp-…"),
            Field(
                "user_token",
                "User token (optional)",
                secret=True,
                required=False,
                help="Lets the agent search messages and set reminders, which Slack keeps from bots.",
                placeholder="xoxp-…",
            ),
            ALLOWED_USERS_FIELD,
        ],
        instructions=[
            "At api.slack.com/apps choose Create New App, from scratch.",
            "Under Socket Mode, switch it on and generate an app-level token (xapp-) with the "
            "connections:write scope.",
            "Under Interactivity & Shortcuts, switch Interactivity on. Socket Mode needs no Request "
            "URL, and the Approve and Deny buttons need Interactivity.",
            f"Under OAuth & Permissions, add the bot scopes {_SLACK_BOT_SCOPES}. files:write lets the "
            "agent send files, and the last three let it resolve the names of senders and channels.",
            "Install the app to your workspace and copy the Bot User OAuth Token (xoxb-).",
            f"For the agent's Slack tools (read, post, react, schedule, canvases) add these bot scopes "
            f"too: {_SLACK_TOOL_SCOPES}. The admin tools stay off until you switch them on, and they "
            "also need channels:manage, groups:write and usergroups:write.",
            "Optional: for search and reminders add the user-token scopes search:read, reminders:read "
            "and reminders:write (plus users.profile:write to set a status), and paste the xoxp- token "
            "below.",
            "Paste the tokens below and press Connect, then invite the bot to a channel or message it.",
        ],
        validate=validate_slack,
    )
    email = ConnectorDescriptor(
        name="email",
        title="Email (IMAP)",
        icon="✉",
        blurb="Read, search and send mail on any IMAP account: Gmail, iCloud, Fastmail or your own server.",
        auth="app_password",
        two_way=False,
        logo="email",
        fields=[
            Field("address", "Email address", placeholder="you@gmail.com"),
            Field(
                "app_password",
                "App password",
                secret=True,
                help="For Gmail or iCloud, create an app password (it needs 2-step verification). It is not your account password.",
            ),
            Field("display_name", "Display name", required=False, help="The name shown as the sender of your mail."),
            Field(
                "imap_host",
                "IMAP host (advanced)",
                required=False,
                help="Needed only for providers that are not detected automatically.",
                placeholder="imap.example.com",
            ),
            Field("imap_port", "IMAP port (advanced)", required=False, placeholder="993"),
            Field("smtp_host", "SMTP host (advanced)", required=False, placeholder="smtp.example.com"),
            Field("smtp_port", "SMTP port (advanced)", required=False, placeholder="587"),
        ],
        instructions=[
            "Gmail: turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords.",
            "iCloud: create an app-specific password at account.apple.com under Sign-In and Security.",
            "Enter your address and the app password below. Gmail, iCloud and Fastmail servers are "
            "detected on their own; for any other provider fill in the IMAP and SMTP hosts.",
            "Google Workspace and Microsoft 365 accounts often have IMAP or app passwords switched off by their administrator.",
        ],
        validate=validate_email,
    )
    whatsapp = ConnectorDescriptor(
        name="whatsapp",
        title="WhatsApp",
        icon="◌",
        blurb="Send WhatsApp messages through Meta's official Cloud API. It is outbound only.",
        auth="token",
        two_way=False,
        brand_color="#25d366",
        logo="whatsapp",
        fields=[
            Field(
                "access_token",
                "Access token",
                secret=True,
                help="From your Meta app's WhatsApp setup page. Use a system-user token for long-lived access.",
            ),
            Field(
                "phone_number_id",
                "Phone number ID",
                help="The ID of the Cloud API phone number, not the phone number itself.",
            ),
        ],
        instructions=[
            "Create a Meta app at developers.facebook.com and add the WhatsApp product to it.",
            "Copy the access token and the phone number ID from the API setup page.",
            "The free test number can message up to 5 verified recipients without business verification.",
            "A free-form message reaches only people who wrote to your number in the last 24 hours; "
            "outside that window only approved templates are delivered.",
        ],
        validate=validate_whatsapp,
    )
    # A personal WhatsApp number linked as a device: its own module describes it.
    from harness.integrations.whatsapp.connector import descriptor as whatsapp_personal

    return [telegram, slack, email, whatsapp, whatsapp_personal()]
