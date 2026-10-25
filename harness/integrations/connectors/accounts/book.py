"""Several accounts for one connector: one profile per account and a pointer to the default.

The layout, for a connector `<c>`:

- `<c>:account:<id>` holds one account's credentials (an email for Gmail and Calendar, the
  field that names an account for the others).
- `<c>:default` holds no credentials once accounts exist: only the pointer to the default
  account, the enabled flag, and any connector-wide setting.

A `<c>:default` that still carries credentials, from a build before accounts existed, is moved
into an account profile the first time the accounts are listed or used. Nothing is asked of the
person.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from harness.safety.secrets import SecretStore


def normalize(value: Any) -> str:
    """An account id in its stored form. Emails want case folding, and UUIDs and numeric ids are
    not affected by it."""
    return str(value or "").strip().lower()


class AccountBook:
    def __init__(
        self,
        connector: str,
        *,
        holds_credentials: Callable[[dict[str, Any]], bool],
        name_legacy_account: Callable[[dict[str, Any]], str],
        pointer_type: Callable[[dict[str, Any]], str] = lambda legacy: "token",
        pointer_keeps: Iterable[str] = (),
    ) -> None:
        """`holds_credentials` says whether a default profile still carries credentials to
        migrate, and `name_legacy_account` what to call the account made of it. `pointer_type`
        gives the `type` of the pointer profile. `pointer_keeps` names the connector-wide keys
        (Gmail's filters) that stay on the pointer and are not copied into the account."""
        self.connector = connector
        self.prefix = f"{connector}:account:"
        self.default_key = f"{connector}:default"
        self._holds_credentials = holds_credentials
        self._name_legacy = name_legacy_account
        self._pointer_type = pointer_type
        self._pointer_keeps = tuple(pointer_keeps)

    # -- reading ----------------------------------------------------------------------------

    def migrate_legacy_default(self, secrets: SecretStore) -> None:
        legacy = secrets.get(self.default_key) or {}
        if not self._holds_credentials(legacy):
            return
        account_id = self._name_legacy(legacy)
        left_behind = {"default_account", *self._pointer_keeps}
        account = {k: v for k, v in legacy.items() if k not in left_behind}
        account.setdefault("account", account_id)
        secrets.put(self.prefix + account_id, account)
        pointer: dict[str, Any] = {
            "type": self._pointer_type(legacy),
            "enabled": bool(legacy.get("enabled", True)),
            "default_account": normalize(legacy.get("default_account")) or account_id,
        }
        for key in self._pointer_keeps:
            if legacy.get(key):
                pointer[key] = legacy[key]
        secrets.put(self.default_key, pointer)

    def list_accounts(self, secrets: SecretStore) -> list[tuple[str, dict[str, Any]]]:
        """(id, profile) for every connected account, sorted by id."""
        self.migrate_legacy_default(secrets)
        found = []
        for meta in secrets.status():
            key = meta.get("profile", "")
            if key.startswith(self.prefix):
                found.append((key[len(self.prefix):], secrets.get(key) or {}))
        return sorted(found, key=lambda row: row[0])

    def default_account(self, secrets: SecretStore) -> str:
        """The default account: the pointer's if that account still exists, else the first one,
        else ""."""
        accounts = dict(self.list_accounts(secrets))
        pointer = normalize((secrets.get(self.default_key) or {}).get("default_account"))
        if pointer in accounts:
            return pointer
        return next(iter(accounts), "")

    def resolve(self, secrets: SecretStore, account: str = "") -> tuple[str, str, Optional[dict[str, Any]]]:
        """(account id, profile key, profile) for the account asked for, or the default one. The
        profile is None when nothing matches."""
        account_id = normalize(account) or self.default_account(secrets)
        if not account_id:
            return "", "", None
        key = self.prefix + account_id
        return account_id, key, secrets.get(key)

    # -- writing ----------------------------------------------------------------------------

    def add(self, secrets: SecretStore, account_id: str, profile: dict[str, Any], *, pointer_type: str) -> dict[str, Any]:
        """Store one account. The first connected account becomes the default, and adding an id
        that exists replaces its credentials in place."""
        self.migrate_legacy_default(secrets)
        account_id = normalize(account_id)
        if not account_id:
            return {"ok": False, "error": "account id missing"}
        secrets.put(self.prefix + account_id, profile)
        pointer = secrets.get(self.default_key) or {}
        pointer.setdefault("default_account", account_id)
        pointer.setdefault("type", pointer_type)
        pointer["enabled"] = bool(pointer.get("enabled", True))
        secrets.put(self.default_key, pointer)
        return {"ok": True, "account": account_id}

    def set_default(self, secrets: SecretStore, account_id: str, *, pointer_type: str) -> dict[str, Any]:
        account_id = normalize(account_id)
        if not secrets.get(self.prefix + account_id):
            return {"ok": False, "error": "account not connected"}
        pointer = secrets.get(self.default_key) or {}
        pointer["default_account"] = account_id
        pointer.setdefault("type", pointer_type)
        pointer.setdefault("enabled", True)
        secrets.put(self.default_key, pointer)
        return {"ok": True, "default_account": account_id}

    def disconnect(self, secrets: SecretStore, account_id: str) -> dict[str, Any]:
        """Drop one account. The default moves to the next account. Removing the last one removes
        the pointer profile too, unless it still holds something worth keeping (`pointer_keeps`)."""
        account_id = normalize(account_id)
        if not secrets.get(self.prefix + account_id):
            return {"ok": False, "error": "account not connected"}
        secrets.delete(self.prefix + account_id)
        remaining = [found for found, _ in self.list_accounts(secrets)]
        pointer = secrets.get(self.default_key) or {}
        if remaining:
            if normalize(pointer.get("default_account")) == account_id:
                pointer["default_account"] = remaining[0]
                secrets.put(self.default_key, pointer)
        else:
            pointer.pop("default_account", None)
            pointer.pop("managed", None)
            if any(pointer.get(key) for key in self._pointer_keeps):
                secrets.put(self.default_key, pointer)
            else:
                secrets.delete(self.default_key)
        return {"ok": True, "remaining_accounts": len(remaining)}
