"""The connector list the Connectors page shows: what each connector is, and where it stands.

Secrets are never returned, only status and the public identity captured when it connected.
"""

from __future__ import annotations

from time import time
from typing import Any

from harness.safety.secrets import SecretStore

from .. import accounts, gcal_accounts, gmail_accounts
from ..catalog import tool_dicts
from ..catalog_copy import about_for, access_for
from ..descriptors import client_env, list_descriptors, mcp_variant_for
from .experimental import experimental_enabled


def _profile_connected(descriptor, profile: dict[str, Any]) -> bool:
    if not descriptor.available:
        return False
    if descriptor.auth == "none":
        return True
    if descriptor.auth == "pairing":
        # Linked by a phone, not by a field: the number the phone linked is the connection.
        return bool(profile.get("account"))
    required = [f.key for f in descriptor.fields if f.required and f.key != "allowed_users"]
    return bool(profile) and all(bool(profile.get(key)) for key in required)


def _mcp_tokens_present(secrets: SecretStore, name: str) -> bool:
    # Imported here: the MCP package pulls in the MCP SDK, which listing connectors should not
    # pay for unless a profile backed by MCP exists.
    from harness.extensions.mcp.oauth import has_tokens

    return has_tokens(name, secrets)


def _mcp_one_click_ready(descriptor: Any) -> bool:
    """Whether the descriptor's one-click sign-in can actually finish.

    Dynamic client registration needs nothing up front, which is its point. The device and PKCE
    grants need a public client id, from the operator's environment or `.env`.
    Without one there is no flow to start, and offering the button anyway leaves the person on
    the vendor's "we couldn't identify the app" page.
    """
    if not (descriptor.mcp_device_auth or getattr(descriptor, "pkce_auth", False) or getattr(descriptor, "browser_token_auth", False)):
        return True
    return bool((descriptor.oauth_client_id or "").strip() or client_env(descriptor.oauth_client_id_env or ""))


def _is_connected(secrets: SecretStore, descriptor, profile: dict[str, Any], mcp_mode: bool) -> bool:
    # A connector with no manual fields served by a vendor's MCP server holds no credential in its
    # profile; the tokens are the connection. Judged by the profile, "no required fields" would be
    # `all([])`, which is True, and an endpoint choice stored there turned the Postman card green
    # mid-sign-in, before any token existed. So the token store is asked.
    mcp_only = bool(descriptor.mcp_url) and not descriptor.fields
    if (mcp_mode or mcp_only) and not getattr(descriptor, "mcp_api_key_auth", False):
        return _mcp_tokens_present(secrets, descriptor.name)
    # An API-key MCP server has no OAuth row to look at: the pasted key is the credential, and it
    # is in this profile. Asking the OAuth store reported "not connected" for a connector that
    # was fully wired.
    return _profile_connected(descriptor, profile)


def _google_accounts(secrets: SecretStore, module) -> list[dict[str, Any]]:
    """The accounts of a Gmail or Calendar connector. An account whose token expired and has no
    refresh token cannot be renewed silently, so the app offers to reauthorize it."""
    default = module.default_account(secrets)
    rows = []
    for email, profile in module.list_accounts(secrets):
        expires = float(profile.get("expires") or 0)
        rows.append({
            "email": email,
            "default": email == default,
            "scopes": profile.get("scope") or "",
            "needs_reauth": bool(expires and expires < time() and not profile.get("refresh_token")),
        })
    return rows


def _with_accounts(entry: dict[str, Any], profile: dict[str, Any], rows: list, account: Any) -> None:
    entry["accounts"] = rows
    entry["connected"] = bool(rows)
    entry["enabled"] = bool(profile.get("enabled", True)) and bool(rows)
    entry["account"] = account


def _entry(secrets: SecretStore, descriptor, profile: dict[str, Any]) -> dict[str, Any]:
    # One MCP server entry is one connection, so a profile in MCP mode is never an account set.
    # The account blocks below overwrite `connected` from their account rows, and an MCP connect
    # writes none, which left Notion reading as disconnected (and `enabled` false, so its tools
    # were cut from sessions) while 42 live tools sat on the connection.
    mcp_mode = bool(descriptor.mcp_url) and profile.get("mode") == "mcp"
    connected = _is_connected(secrets, descriptor, profile, mcp_mode)
    entry = {
        "name": descriptor.name,
        "title": descriptor.title,
        "icon": descriptor.icon,
        "blurb": descriptor.blurb,
        # The copy of the detail page before connecting: an About paragraph (the app leaves the
        # group out when it is empty) and honest Access bullets.
        "about": about_for(descriptor.name),
        "access": access_for(descriptor.name),
        "auth": descriptor.auth,
        "two_way": descriptor.two_way,
        "channels": descriptor.channels,
        "available": descriptor.available,
        "brand_color": descriptor.brand_color,
        "logo": descriptor.logo,
        "aliases": list(descriptor.aliases),
        # A local browser one-click. Two shapes qualify: a vendor-hosted MCP server, or a connector
        # with hand-written tools whose sign-in is still a local browser flow (the Google
        # Workspace set, whose MCP servers refuse calls outside Developer Preview). Keyed on
        # `mcp_url` alone, all six Google cards had no button. It is advertised only if it can run
        # (a build with no client id cannot start the flow, and a tab that only reports that is a
        # UI that lies), and never for an API-key MCP server, which has no browser flow.
        "one_click": (
            bool(descriptor.mcp_url) or getattr(descriptor, "pkce_auth", False) or getattr(descriptor, "browser_token_auth", False)
        )
        and not getattr(descriptor, "mcp_api_key_auth", False)
        and _mcp_one_click_ready(descriptor),
        "fields": [f.to_dict() for f in descriptor.fields],
        # Multi-endpoint MCP servers (Postman): the endpoints to choose between and the one in
        # use. The URLs stay on the server; the app sends back a key and never an address.
        "mcp_variants": [v.to_dict() for v in descriptor.mcp_variants],
        "mcp_variant": mcp_variant_for(descriptor, profile),
        "instructions": descriptor.instructions,
        "connected": connected,
        "account": profile.get("account"),
        "enabled": bool(profile.get("enabled", True)) and connected,
        "allowed_users": list(profile.get("allowed_users") or []),
        "tools": tool_dicts(secrets, descriptor.name),
        "experimental": descriptor.experimental,
        "risk_notice": descriptor.risk_notice,
        "mode": profile.get("mode") or "",  # "mcp" once the vendor's MCP server serves it
    }

    if descriptor.name == "slack":
        # Whether the optional user token (search, reminders, status) is set, so the page can offer
        # to add one. Never the token.
        from .. import slack_user_token

        entry["has_user_token"] = slack_user_token.has_user_token(secrets)
    if not mcp_mode and descriptor.name == "gmail":
        # Each `gmail:account:*` profile is one mailbox; `:default` only holds the default pointer
        # and the privacy filters.
        default = gmail_accounts.default_account(secrets)
        _with_accounts(entry, profile, _google_accounts(secrets, gmail_accounts), default or None)
        entry["filters"] = gmail_accounts.get_filters(secrets)
    if not mcp_mode and descriptor.name == "google_calendar":
        default = gcal_accounts.default_account(secrets)
        _with_accounts(entry, profile, _google_accounts(secrets, gcal_accounts), default or None)
    if descriptor.mcp_toolsets is not None:
        # The vendor server's tool groups. Before a connection they say what the server serves; on
        # one served by it they carry the choice and what each group still needs from the token.
        from harness.extensions.mcp import device_auth

        granted = device_auth.granted_scopes(descriptor.name, secrets) if mcp_mode else None
        entry["toolsets"] = descriptor.mcp_toolsets.catalog(profile, granted)
    if not mcp_mode and descriptor.account_field:
        # Generic accounts: each `<name>:account:*` profile is one account.
        rows = accounts.account_rows(secrets, descriptor.name)
        default_id = accounts.default_account(secrets, descriptor.name)
        default_row = next((row for row in rows if row["account_id"] == default_id), None)
        _with_accounts(entry, profile, rows, (default_row or {}).get("name") or None)
    return entry


def connector_list(secrets: SecretStore) -> list[dict[str, Any]]:
    show_experimental = experimental_enabled(secrets)
    # Experimental connectors are invisible, not just disabled, until the person opts in. Hiding
    # them here also drops their tools from engine builds, so switching the setting off cuts
    # access at once.
    return [
        _entry(secrets, descriptor, secrets.get(f"{descriptor.name}:default") or {})
        for descriptor in list_descriptors()
        if show_experimental or not descriptor.experimental
    ]
