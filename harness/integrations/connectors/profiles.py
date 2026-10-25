"""Finding the stored credentials a tool call needs.

Each helper returns the profile together with an error result for the tool to hand back, and
renews a Google token that has aged out before returning it. Gmail and Calendar have their own
account modules (`gmail_accounts`, `gcal_accounts`); every other account-shaped connector uses
the generic `accounts` layer.
"""

from __future__ import annotations

from typing import Any, Optional

from harness.safety.secrets import SecretStore

#: The `account` property of the generic account-shaped tools.
GENERIC_ACCOUNT_PROP = {
    "type": "string",
    "description": "Which connected account to use (default account when empty)",
}
MAILBOX_ACCOUNT_PROP = {"type": "string", "description": "Mailbox email to use; omit for the default account."}
CALENDAR_ACCOUNT_PROP = {
    "type": "string",
    "description": "Google account email to use; omit for the default account.",
}

Failure = Optional[dict[str, str]]


def google_refreshed(secrets: SecretStore, connector: str, key: str, profile: dict[str, Any]) -> dict[str, Any]:
    """The profile with its Google access token renewed in place when it has aged out.

    One hook rather than a check inside 27 tools. Google access tokens live an hour, so without
    it every Workspace tool works right after sign-in and fails for the rest of the day, which
    is what the pasted-token flow did. The token is also noted for renewal on a 401
    (`google_auth.note_issued`). Only profiles written by the browser sign-in carry
    `google_client_id`, so a hand-pasted token is left alone.
    """
    if profile.get("ms_client_id"):
        # A Microsoft sign-in renews the same way, against Microsoft's endpoint.
        from .microsoft_auth import refreshed

        return refreshed(secrets, connector, key, profile)
    if not profile.get("google_client_id"):
        return profile
    from .google_auth import fresh_token, note_issued

    _token, err = fresh_token(secrets, connector, key, profile)
    if err:
        return profile
    current = secrets.get(key) or profile
    note_issued(secrets, key, current)
    return current


def default_profile(secrets: SecretStore, name: str, *keys: str) -> tuple[Optional[dict[str, Any]], Failure]:
    """(`<name>:default` profile, None), or (None, why) when one of `keys` is missing."""
    profile = google_refreshed(secrets, name, f"{name}:default", secrets.get(f"{name}:default") or {})
    missing = [key for key in keys if not profile.get(key)]
    if missing:
        return None, {"error": f"{name} is not connected; missing {', '.join(missing)}"}
    return profile, None


def account_profile(
    secrets: SecretStore, connector: str, account: str = "", *keys: str
) -> tuple[str, Optional[dict[str, Any]], Failure]:
    """(account id, profile, error) for a connector on the generic accounts layer: the account
    asked for, or the default one."""
    from . import accounts

    account_id, key, profile = accounts.resolve(secrets, connector, account)
    if profile is None:
        hint = f"no {connector} account matching {account!r}" if account else f"{connector} is not connected"
        return "", None, {"error": hint}
    profile = google_refreshed(secrets, connector, key, profile)
    missing = [name for name in keys if not profile.get(name)]
    if missing:
        return account_id, None, {"error": f"{connector} is not connected; missing {', '.join(missing)}"}
    return account_id, profile, None


def account_result(account_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """The result with the account that served it named, so approvals and transcripts say which
    one once more than one is connected."""
    if isinstance(result, dict) and account_id:
        return {"account": account_id, **result}
    return result


def gmail_profile(secrets: SecretStore, account: str = "") -> tuple[str, Optional[dict[str, Any]], Failure]:
    """(email, profile, error) for the mailbox asked for, or the default one
    (`gmail:account:<email>`)."""
    from . import gmail_accounts

    email, key, profile = gmail_accounts.resolve(secrets, account)
    if profile is None:
        return "", None, {"error": f"no gmail account matching {account!r}" if account else "gmail is not connected"}
    profile = google_refreshed(secrets, "gmail", key, profile)
    if not profile.get("access_token"):
        return "", None, {"error": f"gmail account {email} has no usable token"}
    return email, profile, None


def gcal_profile(secrets: SecretStore, account: str = "") -> tuple[str, Optional[dict[str, Any]], Failure]:
    """(email, profile, error) for the Google account asked for, or the default one
    (`google_calendar:account:<email>`)."""
    from . import gcal_accounts

    email, key, profile = gcal_accounts.resolve(secrets, account)
    if profile is None:
        hint = f"no google calendar account matching {account!r}" if account else "google calendar is not connected"
        return "", None, {"error": hint}
    profile = google_refreshed(secrets, "google_calendar", key, profile)
    if not profile.get("access_token"):
        return "", None, {"error": f"google calendar account {email} has no usable token"}
    return email, profile, None


def gcal_result(email: str, result: dict[str, Any]) -> dict[str, Any]:
    """A successful result with the calendar's account named, as the Gmail tools do."""
    if result.get("ok"):
        result["account"] = email
    return result
