"""Connecting a connector: checking what was entered and storing it."""

from __future__ import annotations

from typing import Any

from harness.safety.secrets import SecretStore

from .. import accounts
from ..descriptors import get_descriptor, mcp_url_for
from .experimental import experimental_enabled


def _entered_value(field, entered: dict[str, Any], stored: dict[str, Any]) -> str:
    """What the form holds for a field, keeping what is stored when the form left it blank.

    A connected connector's secrets are masked in the app, which shows the placeholder (`xoxb-…`).
    A blank value, or the mask itself, means "keep what is stored" and never "overwrite with the
    mask": resubmitting the form once reset a real token to its six-character placeholder.
    """
    value = str(entered.get(field.key) or "").strip()
    if field.key == "allowed_users":
        return value  # a list in storage and comma-separated in the form; handled apart
    if not value or (field.secret and value == (field.placeholder or "").strip()):
        return str(stored.get(field.key) or "").strip()
    return value


def connect_connector(
    secrets: SecretStore,
    name: str,
    fields: dict[str, Any],
    *,
    validate: bool = True,
    acknowledged: bool = False,
) -> dict[str, Any]:
    """Store a connector's credentials. `validate=False` skips the network check (tests)."""
    descriptor = get_descriptor(name)
    if descriptor is None or not descriptor.available:
        return {"ok": False, "error": "unknown or unavailable connector"}
    if descriptor.experimental:
        if not experimental_enabled(secrets):
            return {"ok": False, "error": "experimental connectors are disabled"}
        if not acknowledged:
            return {"ok": False, "error": "risk acknowledgment required", "risk_notice": descriptor.risk_notice}

    stored = secrets.get(f"{name}:default") or {}
    values = {f.key: _entered_value(f, fields, stored) for f in descriptor.fields}
    missing = [f.label for f in descriptor.fields if f.required and not values.get(f.key)]
    if missing:
        return {"ok": False, "error": "missing: " + ", ".join(missing)}

    allowed = sorted({u.strip() for u in values.get("allowed_users", "").split(",") if u.strip()})
    if not allowed and stored.get("allowed_users"):
        allowed = list(stored["allowed_users"])  # a reconnect must not wipe the live allow-list
    credentials = {k: v for k, v in values.items() if k != "allowed_users" and v}

    identity = None
    if validate and descriptor.validate is not None:
        outcome = descriptor.validate(credentials)
        if not outcome.ok:
            return {"ok": False, "error": outcome.error or "validation failed"}
        identity = outcome.identity

    kind = "oauth" if descriptor.auth == "oauth" else "none" if descriptor.auth == "none" else "token"
    profile: dict[str, Any] = {"type": kind, "enabled": True, **credentials}
    if any(f.key == "allowed_users" for f in descriptor.fields):
        profile["allowed_users"] = allowed
    if identity:
        profile["account"] = identity

    if descriptor.account_field:
        # Connecting adds an account; a second submit with other credentials is a second account
        # and not an overwrite.
        account_id = accounts.derive_account_id(descriptor, profile)
        added = accounts.add_account(secrets, name, account_id, profile)
        if not added.get("ok"):
            return added
        return {"ok": True, "account": identity or account_id, "account_id": account_id}

    if descriptor.mcp_url and getattr(descriptor, "mcp_api_key_auth", False):
        # The tools come from the vendor's MCP server and not from us, so a pasted key is half a
        # connection. Without the server entry the card would go green with no tools. `mode:
        # "mcp"` is what `active_tool_defs` reads.
        from harness.extensions.mcp import config as mcp_config

        mcp_config.put_global_server(
            name,
            {"url": mcp_url_for(descriptor, profile), "auth": "bearer", "requires_approval": False, "enabled": True},
        )
        profile["mode"] = "mcp"

    secrets.put(f"{name}:default", profile)
    return {"ok": True, "account": identity}
