"""Google Calendar's signed-in accounts.

`google_calendar:account:<email>` is one account's tokens; `google_calendar:default`, once there are
accounts, is only which one is the default and whether the connector is on. `AccountBook` owns that
layout and moves an older single-account profile into it.
"""

from __future__ import annotations

from .accounts.google import GoogleAccounts

_ACCOUNTS = GoogleAccounts("google_calendar")

PREFIX = _ACCOUNTS.prefix
DEFAULT_KEY = _ACCOUNTS.default_key

migrate_legacy_default = _ACCOUNTS.migrate_legacy_default
list_accounts = _ACCOUNTS.list_accounts
default_account = _ACCOUNTS.default_account
resolve = _ACCOUNTS.resolve
managed_connect_account = _ACCOUNTS.connect
disconnect_account = _ACCOUNTS.disconnect


def set_default(secrets, email: str):
    return _ACCOUNTS.set_default(secrets, email, pointer_type="oauth")
