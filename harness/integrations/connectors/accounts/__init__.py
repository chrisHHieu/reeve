"""Accounts for the connectors without a module of their own: any connector whose descriptor sets
`account_field`.

`account_field` is the credentials field that names an account (a project id, say), or
`"@identity"`, meaning the identity string the connector's validator returned (the account's
email). Connecting, the connector list, the account routes and the accounts screen all key off it.
Gmail and Calendar use the same `AccountBook` in `gmail_accounts` and `gcal_accounts`.
"""

from __future__ import annotations

from typing import Any, Optional

from harness.safety.secrets import SecretStore

from ..descriptors import ConnectorDescriptor, get_descriptor
from .book import AccountBook, normalize

IDENTITY = "@identity"

__all__ = [
    "AccountBook",
    "IDENTITY",
    "account_rows",
    "add_account",
    "default_account",
    "default_key",
    "derive_account_id",
    "disconnect_account",
    "is_account_connector",
    "list_accounts",
    "migrate_legacy_default",
    "prefix",
    "resolve",
    "set_default",
]


def prefix(connector: str) -> str:
    return f"{connector}:account:"


def default_key(connector: str) -> str:
    return f"{connector}:default"


def is_account_connector(name: str) -> bool:
    descriptor = get_descriptor(name)
    return bool(descriptor and descriptor.account_field)


def derive_account_id(descriptor: ConnectorDescriptor, profile: dict[str, Any]) -> str:
    """The id that names an account: the designated credentials field, else the identity the
    validator returned (stored as `account` when connecting). "default" only when neither exists,
    so a migration can never strand a profile."""
    if descriptor.account_field and descriptor.account_field != IDENTITY:
        return normalize(profile.get(descriptor.account_field)) or normalize(profile.get("account")) or "default"
    return normalize(profile.get("account")) or "default"


def _book(connector: str) -> AccountBook:
    descriptor = get_descriptor(connector)
    credential_keys = [f.key for f in descriptor.fields if f.key != "allowed_users"] if descriptor else []
    return AccountBook(
        connector,
        holds_credentials=lambda legacy: any(legacy.get(key) for key in credential_keys),
        name_legacy_account=lambda legacy: derive_account_id(descriptor, legacy),
        pointer_type=lambda legacy: legacy.get("type") or "token",
    )


def migrate_legacy_default(secrets: SecretStore, connector: str) -> None:
    _book(connector).migrate_legacy_default(secrets)


def list_accounts(secrets: SecretStore, connector: str) -> list[tuple[str, dict[str, Any]]]:
    return _book(connector).list_accounts(secrets)


def default_account(secrets: SecretStore, connector: str) -> str:
    return _book(connector).default_account(secrets)


def resolve(secrets: SecretStore, connector: str, account: str = "") -> tuple[str, str, Optional[dict[str, Any]]]:
    return _book(connector).resolve(secrets, account)


def add_account(secrets: SecretStore, connector: str, account_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Store one account, from a manual connect or a managed sign-in alike."""
    return _book(connector).add(secrets, account_id, profile, pointer_type=profile.get("type") or "token")


def set_default(secrets: SecretStore, connector: str, account_id: str) -> dict[str, Any]:
    return _book(connector).set_default(secrets, account_id, pointer_type="token")


def disconnect_account(secrets: SecretStore, connector: str, account_id: str) -> dict[str, Any]:
    return _book(connector).disconnect(secrets, account_id)


def account_rows(secrets: SecretStore, connector: str) -> list[dict[str, Any]]:
    """What the connector list shows per account: its id, a display name (the identity captured
    when connecting, else the id) and whether it is the default."""
    default = default_account(secrets, connector)
    return [
        {"account_id": account_id, "name": str(profile.get("account") or account_id), "default": account_id == default}
        for account_id, profile in list_accounts(secrets, connector)
    ]
