"""Gmail's signed-in mailboxes, and the "Never show agents" filters over all of them.

`gmail:account:<email>` is one mailbox's tokens. `gmail:default` is which mailbox is the default,
whether Gmail is on, and the filters, which hold for every mailbox; `AccountBook` owns the layout.

The Gmail tools apply the filters here on the desktop, which is where both the mail and the
person's rules are. A message a filter catches is simply not in what the agent is given, with no
placeholder to reason about; the person sees on the tool card how many were held back, and the
audit log gets the rule and the count, never the message.
"""

from __future__ import annotations

from typing import Any, Optional

from harness.safety.secrets import SecretStore

from .accounts.book import normalize
from .accounts.google import GoogleAccounts

_ACCOUNTS = GoogleAccounts("gmail", pointer_keeps=("filters",))

PREFIX = _ACCOUNTS.prefix
DEFAULT_KEY = _ACCOUNTS.default_key

migrate_legacy_default = _ACCOUNTS.migrate_legacy_default
list_accounts = _ACCOUNTS.list_accounts
default_account = _ACCOUNTS.default_account
resolve = _ACCOUNTS.resolve
managed_connect_account = _ACCOUNTS.connect
# The filters are the person's policy, not a credential: they outlive the last mailbox.
disconnect_account = _ACCOUNTS.disconnect


def set_default(secrets: SecretStore, email: str) -> dict[str, Any]:
    return _ACCOUNTS.set_default(secrets, email, pointer_type="oauth")


# -- "Never show agents" ------------------------------------------------------------------------


def get_filters(secrets: SecretStore) -> dict[str, list[str]]:
    stored = _ACCOUNTS.pointer(secrets).get("filters") or {}
    return {"senders": list(stored.get("senders") or []), "labels": list(stored.get("labels") or [])}


def set_filters(secrets: SecretStore, senders: Optional[list[str]] = None, labels: Optional[list[str]] = None) -> dict[str, Any]:
    """Replace the senders, the labels or both; a list given as None stays as it is. A sender is
    an address or `@domain`; a label is a Gmail label's name, matched in any case."""
    filters = get_filters(secrets)
    if senders is not None:
        filters["senders"] = sorted({address for address in map(normalize, senders) if address})
    if labels is not None:
        filters["labels"] = sorted({name for name in (str(label).strip() for label in labels) if name})
    _ACCOUNTS.put_pointer(secrets, filters=filters)
    return {"ok": True, "filters": filters}


def sender_matches(address: str, rules: list[str]) -> bool:
    """`ann@x.com` catches that address only; `@x.com` catches every address that ends with it."""
    address = normalize(address)
    if not address:
        return False
    return any(address.endswith(rule) if rule.startswith("@") else address == rule for rule in map(normalize, rules) if rule)
