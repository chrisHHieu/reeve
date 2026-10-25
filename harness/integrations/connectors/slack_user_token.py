"""The optional Slack user token: what search, reminders and status need.

Slack answers those methods only for a user token. The Socket Mode app holds a bot token, so
the user token is added beside it by the person who wants those tools. It is checked against
`auth.test` before it is kept.
"""

from __future__ import annotations

from typing import Any

from harness.safety.secrets import SecretStore
from . import slack_api

_KEY = "slack:default"


def set_user_token(secrets: SecretStore, token: str) -> dict[str, Any]:
    profile = dict(secrets.get(_KEY) or {})
    if not profile.get("bot_token"):
        return {"ok": False, "error": "slack is not connected; connect it in Connectors first"}
    token = (token or "").strip()
    if not token:
        profile.pop("user_token", None)
        secrets.put(_KEY, profile)
        return {"ok": True, "removed": True}
    if not token.startswith("xoxp-"):
        return {"ok": False, "error": "a Slack user token starts with xoxp-"}
    try:
        resp = slack_api._send(
            "POST", slack_api.api_base() + "auth.test",
            headers={"Authorization": f"Bearer {token}"}, params=None, data=None, json=None,
        )
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"could not reach Slack: {exc.__class__.__name__}"}
    if not data.get("ok"):
        return {"ok": False, "error": slack_api.explain(str(data.get("error") or "invalid token"), data)}
    profile["user_token"] = token
    secrets.put(_KEY, profile)
    return {"ok": True, "user": data.get("user", ""), "team": data.get("team", "")}


def has_user_token(secrets: SecretStore) -> bool:
    return bool((secrets.get(_KEY) or {}).get("user_token"))
