"""Disconnecting a connector."""

from __future__ import annotations

from typing import Any

from harness.safety.secrets import SecretStore

from .. import accounts, gcal_accounts, gmail_accounts


def disconnect_connector(secrets: SecretStore, name: str) -> dict[str, Any]:
    """Forget a connector's credentials. `ok` says whether anything was removed."""
    removed = False

    if accounts.is_account_connector(name):
        for account_id, _profile in accounts.list_accounts(secrets, name):
            removed = secrets.delete(accounts.prefix(name) + account_id) or removed
    # Disconnecting the whole connector drops every mailbox and calendar account, and Gmail's
    # filters with them: an explicit full reset. Removing one account is done on its own page.
    if name == "gmail":
        for email, _profile in gmail_accounts.list_accounts(secrets):
            removed = secrets.delete(gmail_accounts.PREFIX + email) or removed
    if name == "google_calendar":
        for email, _profile in gcal_accounts.list_accounts(secrets):
            removed = secrets.delete(gcal_accounts.PREFIX + email) or removed

    if (secrets.get(f"{name}:default") or {}).get("mode") == "mcp":
        # Served by the vendor's MCP server: forget the OAuth tokens and the dynamic client
        # registration, and remove the seeded server entry, so a reconnect starts a fresh flow.
        from harness.extensions.mcp import config as mcp_config
        from harness.extensions.mcp import oauth as mcp_oauth

        removed = mcp_oauth.sign_out(name, secrets) or removed
        mcp_config.delete_global_server(name)
    return {"ok": secrets.delete(f"{name}:default") or removed}
