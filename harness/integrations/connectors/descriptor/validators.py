"""The checks made when credentials are entered: one cheap request each, and the identity it
reveals to show back."""

from __future__ import annotations

from typing import Callable, Optional

from .model import ValidationResult


def whoami(
    method: str,
    url: str,
    *,
    headers: dict,
    identity: Callable[[dict], str],
    json: Optional[dict] = None,
) -> ValidationResult:
    """A one-shot check: the call must succeed and `identity` must be readable from the answer."""
    import httpx

    try:
        response = httpx.request(method, url, headers=headers, json=json, timeout=15)
        data = response.json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if response.status_code >= 400:
        detail = None
        if isinstance(data, dict):
            detail = data.get("message") or data.get("error") or data.get("error_summary")
        return ValidationResult(False, error=str(detail or f"HTTP {response.status_code}"))
    try:
        return ValidationResult(True, identity=str(identity(data)))
    except Exception:
        return ValidationResult(False, error="unexpected response from API")


def _bearer(creds: dict, field: str = "access_token") -> dict:
    return {"Authorization": f"Bearer {creds.get(field, '')}"}


def _key_tail(creds: dict) -> Callable[[dict], str]:
    """An identity for an endpoint that names no account: the tail of the key, so two keys stay
    distinguishable in the accounts list."""
    return lambda _data, key=str(creds.get("api_key", "")): f"key …{key[-6:]}"


def validate_telegram(creds: dict) -> ValidationResult:
    import httpx

    try:
        data = httpx.get(f"https://api.telegram.org/bot{creds.get('bot_token', '')}/getMe", timeout=15).json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if data.get("ok"):
        return ValidationResult(True, identity="@" + str(data["result"].get("username", "bot")))
    return ValidationResult(False, error=data.get("description") or "invalid bot token")


def validate_email(creds: dict) -> ValidationResult:
    from ..mail import validate_email_account

    ok, identity, error = validate_email_account(creds)
    return ValidationResult(ok, identity=identity or None, error=error or None)


def validate_slack(creds: dict) -> ValidationResult:
    import httpx

    try:
        data = httpx.post("https://slack.com/api/auth.test", headers=_bearer(creds, "bot_token"), timeout=15).json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if data.get("ok"):
        return ValidationResult(True, identity=f"{data.get('team', '?')} / {data.get('user', 'bot')}")
    return ValidationResult(False, error=data.get("error") or "invalid bot token")


def validate_notion(creds: dict) -> ValidationResult:
    return whoami(
        "GET",
        "https://api.notion.com/v1/users/me",
        headers={**_bearer(creds), "Notion-Version": "2022-06-28"},
        identity=lambda d: (d.get("bot") or {}).get("workspace_name") or d["name"],
    )


def validate_trello(creds: dict) -> ValidationResult:
    """Whoami against `/members/me`. Trello answers a bad key and a bad token with the same 401,
    so the message names both; the identity is the member's username."""
    import httpx

    key, token = str(creds.get("api_key") or "").strip(), str(creds.get("token") or "").strip()
    try:
        response = httpx.get(
            "https://api.trello.com/1/members/me",
            params={"fields": "username,fullName"},
            headers={"Authorization": f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'},
            timeout=15,
        )
    except Exception as exc:
        return ValidationResult(False, error=f"Trello did not answer: {exc.__class__.__name__}")
    if response.status_code in (400, 401, 403):
        return ValidationResult(
            False,
            error="Trello refused the key or the token. Copy the API key from your Power-Up's API key "
            "page, then make a token from the Token link beside it.",
        )
    if response.status_code >= 400:
        return ValidationResult(False, error=f"HTTP {response.status_code}")
    try:
        member = response.json()
        return ValidationResult(True, identity=str(member.get("username") or member["id"]))
    except Exception:
        return ValidationResult(False, error="Trello's answer could not be read")


def validate_redmine(creds: dict) -> ValidationResult:
    """Whoami against `/users/current.json`, with the one hint its error needs.

    Redmine ships with the REST API switched off. Until an administrator ticks Administration,
    Settings, API, "Enable REST web service", every API call answers 401 exactly as a wrong key
    does, and the body is a login page and not JSON. A bare "HTTP 401" would send the person
    looking for a key that was never the problem, so both causes are named.
    """
    import httpx

    base = str(creds.get("base_url") or "").rstrip("/")
    try:
        response = httpx.get(
            f"{base}/users/current.json",
            headers={"X-Redmine-API-Key": str(creds.get("api_key", ""))},
            timeout=15,
        )
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if response.status_code in (401, 403):
        return ValidationResult(
            False,
            error="Redmine refused the key. Either the API key is wrong, or an "
            "administrator has not enabled the REST API yet "
            "(Administration -> Settings -> API).",
        )
    if response.status_code >= 400:
        return ValidationResult(False, error=f"HTTP {response.status_code}")
    try:
        user = response.json()["user"]
        return ValidationResult(True, identity=str(user.get("login") or user["id"]))
    except Exception:
        # A 200 that is not the JSON asked for means the URL points at something that is not a
        # Redmine site: a proxy, a single-sign-on wall, a mistyped host.
        return ValidationResult(False, error="that URL answered, but not as a Redmine API would")


def validate_firecrawl(creds: dict) -> ValidationResult:
    """Check the key on a free endpoint and not on the MCP server.

    `tools/list` cannot validate it: Firecrawl returns the full tool list for any Authorization
    header, valid or not (tried with an invented key), and only a real tool call reports a bad
    key, at the price of a credit. The credit-usage read answers 401 for a bad key and costs
    nothing.
    """
    return whoami(
        "GET", "https://api.firecrawl.dev/v2/team/credit-usage",
        headers=_bearer(creds, "api_key"), identity=_key_tail(creds),
    )


def validate_whatsapp(creds: dict) -> ValidationResult:
    return whoami(
        "GET",
        f"https://graph.facebook.com/v21.0/{creds.get('phone_number_id', '')}",
        headers=_bearer(creds),
        identity=lambda d: d["display_phone_number"],
    )


def validate_figma(creds: dict) -> ValidationResult:
    return whoami(
        "GET", "https://api.figma.com/v1/me",
        headers={"X-Figma-Token": creds.get("access_token", "")}, identity=lambda d: d["email"],
    )


def validate_google_drive(creds: dict) -> ValidationResult:
    return whoami(
        "GET", "https://www.googleapis.com/drive/v3/about?fields=user",
        headers=_bearer(creds), identity=lambda d: d["user"]["emailAddress"],
    )


def validate_canva(creds: dict) -> ValidationResult:
    return whoami(
        "GET", "https://api.canva.com/rest/v1/users/me/profile",
        headers=_bearer(creds), identity=lambda d: d["profile"]["display_name"],
    )


def validate_outlook(creds: dict) -> ValidationResult:
    return whoami(
        "GET", "https://graph.microsoft.com/v1.0/me",
        headers=_bearer(creds), identity=lambda d: d.get("mail") or d["userPrincipalName"],
    )
