"""Microsoft 365 connectors: Outlook, OneDrive and Teams, on one browser sign-in.

Each is its own card asking only its own scopes, so a person who turns on Outlook is never asked
for their files. The sign-in and its renewal are `microsoft_auth`.
"""

from __future__ import annotations

from ..descriptor.model import ConnectorDescriptor, Field
from ..descriptor.validators import validate_outlook

def _microsoft(scope: str) -> dict:
    """What every Microsoft 365 connector shares: the browser sign-in against the operator's Entra
    app, renewed by `microsoft_auth`, with a pasted Graph token as the manual way.

    No client id ships. One click needs `REEVE_MICROSOFT_CLIENT_ID` in the environment or the state
    folder's `.env`: an Entra app registered for "any organizational directory and personal
    Microsoft accounts" as a public client. Without it the card offers the manual way."""
    from ..microsoft_auth import AUTHORIZE, TOKEN

    return dict(
        auth="oauth",
        two_way=False,
        pkce_auth=True,
        pkce_provider="microsoft",
        pkce_auth_endpoint=AUTHORIZE,
        oauth_token_endpoint=TOKEN,
        oauth_scope=scope,
        oauth_client_id_env="REEVE_MICROSOFT_CLIENT_ID",
        # `select_account` lets a person with a work and a personal account pick each time.
        pkce_auth_extra=(("prompt", "select_account"),),
        fields=[Field("access_token", "Graph access token", secret=True, help="Lasts about an hour and is not renewed; one click is.")],
        validate=validate_outlook,
        available=True,
        # Each account is keyed by its email, the multi-account shape Gmail and Drive use.
        account_field="@identity",
    )


def _outlook() -> ConnectorDescriptor:
    return ConnectorDescriptor(
        name="outlook",
        title="Outlook",
        icon="◎",
        blurb="Microsoft 365 mail, calendar and people: read, reply, file and send email, plan meetings and answer invitations.",
        brand_color="#0078d4",
        logo="outlook",
        aliases=("calendar", "email", "mail", "microsoft", "office", "hotmail", "outlook.com", "exchange"),
        instructions=[
            "One click signs in with Microsoft in your browser, a work or a personal account. It renews "
            "itself; nothing is typed.",
            "Manual: paste a Microsoft Graph access token with Mail and Calendar scopes. It lasts about an "
            "hour and is not renewed.",
        ],
        **_microsoft("Mail.ReadWrite Mail.Send Calendars.ReadWrite Contacts.Read People.Read MailboxSettings.Read"),
    )


def _onedrive() -> ConnectorDescriptor:
    return ConnectorDescriptor(
        name="onedrive",
        title="OneDrive",
        icon="☁",
        blurb="Your OneDrive files and the ones shared with you: find, read, save, upload, organize and share.",
        brand_color="#0364b8",
        logo="onedrive",
        aliases=("files", "documents", "microsoft", "office", "word", "excel", "powerpoint", "cloud storage"),
        instructions=[
            "One click signs in with Microsoft in your browser, a work or a personal account. It renews "
            "itself; nothing is typed.",
            "Manual: paste a Microsoft Graph access token with the Files scope. It lasts about an hour.",
        ],
        **_microsoft("Files.ReadWrite.All"),
    )


def _teams() -> ConnectorDescriptor:
    # No `ChannelMessage.Read.All`: only an admin can grant it, and asking for it would stop the
    # whole sign-in for everyone else (tool_builders/teams.py).
    return ConnectorDescriptor(
        name="teams",
        title="Microsoft Teams",
        icon="◫",
        blurb="Your Teams chats and channels: read and send chat messages, start a chat, post and reply in channels.",
        brand_color="#5059c9",
        logo="teams",
        aliases=("teams", "chat", "microsoft", "channels", "messages", "office"),
        instructions=[
            "One click signs in with Microsoft in your browser. Teams needs a work or school account; "
            "a personal Microsoft account has no Teams to reach.",
            "Manual: paste a Microsoft Graph access token with the Chat and Channel scopes. It lasts about an hour.",
        ],
        **_microsoft("Chat.ReadWrite Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send User.ReadBasic.All"),
    )


def build() -> list[ConnectorDescriptor]:
    return [_outlook(), _onedrive(), _teams()]
