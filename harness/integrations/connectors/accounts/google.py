"""The account book of a Google connector (Gmail, Calendar): accounts named by email, OAuth only.

A Google sign-in returns the email it signed in as, and that email names the account: signing in
as an address already here replaces its tokens where they are.
"""

from __future__ import annotations

from typing import Any, Iterable

from harness.safety.secrets import SecretStore

from .book import AccountBook, normalize


class GoogleAccounts(AccountBook):
    def __init__(self, connector: str, *, pointer_keeps: Iterable[str] = ()) -> None:
        super().__init__(
            connector,
            holds_credentials=lambda stored: bool(stored.get("access_token")),
            name_legacy_account=lambda stored: normalize(stored.get("account")) or "default",
            pointer_type=lambda stored: "oauth",
            pointer_keeps=pointer_keeps,
        )

    def connect(self, secrets: SecretStore, profile: dict[str, Any]) -> dict[str, Any]:
        """Keep the account a sign-in returned. Whatever the connector's pointer said before, a
        sign-in leaves it switched on and of type oauth."""
        email = normalize(profile.get("account"))
        if not email:
            self.migrate_legacy_default(secrets)
            return {"ok": False, "error": "google account email missing from callback"}
        added = self.add(secrets, email, profile, pointer_type="oauth")
        pointer = secrets.get(self.default_key) or {}
        secrets.put(self.default_key, {**pointer, "type": "oauth", "enabled": True})
        return added

    def pointer(self, secrets: SecretStore) -> dict[str, Any]:
        """The connector-wide profile, as it is stored."""
        return secrets.get(self.default_key) or {}

    def put_pointer(self, secrets: SecretStore, **fields: Any) -> None:
        """Set connector-wide fields; a pointer made here is an enabled OAuth one."""
        pointer = {"type": "oauth", "enabled": True, **self.pointer(secrets), **fields}
        secrets.put(self.default_key, pointer)
