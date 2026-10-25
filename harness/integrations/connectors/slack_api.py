"""The Slack Web API client the tools use: which token, what a failure says.

Kept apart from the tools so two decisions live in one place:

* **Which token.** Most methods take the bot token the connector already holds. Search,
  reminders and status take only a user token; that is stored beside it (`user_token`, in
  the workspace's profile) and, when absent, the tool says so instead of failing with
  `not_allowed_token_type` (Slack's own answer, which no model can act on).
* **What a failure says.** Slack answers `{"ok": false, "error": "not_in_channel"}` with
  HTTP 200. Each error a model can do something about becomes the sentence that says what
  to do; the rest keep Slack's string, because guessing at one would be worse than naming it.

A 429 is waited out once (Slack's `Retry-After`, capped): a burst of reads should not turn
into an error the model then retries itself, in a loop.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from harness.safety.secrets import SecretStore

#: Longest a `Retry-After` is honoured. Past this the call fails and says when to retry.
MAX_WAIT = 10.0
TIMEOUT = 30.0


def api_base() -> str:
    return os.environ.get("SLACK_API_URL", "https://slack.com/api/")


def token_for(secrets: SecretStore, kind: str) -> tuple[str, Optional[dict[str, str]]]:
    profile = secrets.get("slack:default") or {}
    if kind == "user":
        token = str(profile.get("user_token") or "")
        if not token:
            return "", {
                "error": "this needs a Slack user token (search, reminders and status are not open to bots); "
                "add one under Slack in Connectors, or ask the person to"
            }
        return token, None
    token = str(profile.get("bot_token") or "")
    if not token:
        return "", {"error": "slack is not connected; connect it in Connectors first"}
    return token, None


#: Slack's error strings a model can act on, as the sentence that says how.
_HINTS = {
    "not_in_channel": "the bot is not in that channel; call slack_join_channel (public) or ask someone to invite it",
    "channel_not_found": "no such channel, or a private one the bot is not in",
    "user_not_found": "no such person in this workspace",
    "message_not_found": "no message with that timestamp in that channel",
    "thread_not_found": "no thread with that timestamp in that channel",
    "invalid_auth": "the Slack token is no longer valid; reconnect Slack in Connectors",
    "token_revoked": "the Slack token was revoked; reconnect Slack in Connectors",
    "account_inactive": "the Slack token belongs to a deactivated account; reconnect Slack in Connectors",
    "is_archived": "that channel is archived",
    "cant_delete_message": "only a message the bot posted can be deleted",
    "edit_window_closed": "that message can no longer be edited",
    "restricted_action": "the workspace's settings do not let the bot do that",
    "not_allowed_token_type": "this method needs a user token, not the bot token",
    "name_taken": "a channel or group with that name already exists",
    "no_permission": "the bot may not do that here",
    "ratelimited": "Slack is rate-limiting this token; wait a moment and try once more",
}


def explain(error: str, data: dict[str, Any]) -> str:
    if error == "missing_scope":
        needed = data.get("needed") or "a scope the app was not granted"
        return (
            f"the Slack app lacks the permission `{needed}`; add it to the app's scopes and reinstall it "
            "(a workspace admin may need to approve)"
        )
    return _HINTS.get(error, error)


def _send(method: str, url: str, *, headers: dict[str, str], params: Optional[dict[str, Any]], data: Optional[dict[str, Any]], json: Any):
    """One HTTP exchange. Separate so tests replace exactly this."""
    import httpx

    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        return client.request(method, url, headers=headers, params=params, data=data, json=json)


def call(
    secrets: SecretStore,
    method: str,
    *,
    kind: str = "bot",
    params: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    as_json: bool = False,
    read: bool = False,
) -> dict[str, Any]:
    """`{"ok": True, "data": …}` or `{"error": …}`. Never raises."""
    token, err = token_for(secrets, kind)
    if err:
        return err
    headers = {"Authorization": f"Bearer {token}"}
    if as_json:
        headers["Content-Type"] = "application/json; charset=utf-8"
    for attempt in (0, 1):
        try:
            if read:
                resp = _send("GET", api_base() + method, headers=headers, params=_clean(params), data=None, json=None)
            elif as_json:
                resp = _send("POST", api_base() + method, headers=headers, params=None, data=None, json=_clean(body))
            else:
                resp = _send("POST", api_base() + method, headers=headers, params=None, data=_clean(body), json=None)
        except Exception as exc:  # transport: DNS, TLS, timeout
            return {"error": f"could not reach Slack: {exc.__class__.__name__}"}
        if resp.status_code == 429:
            wait = _retry_after(resp)
            if attempt == 0 and wait <= MAX_WAIT:
                time.sleep(wait)
                continue
            return {"error": f"Slack is rate-limiting this token; retry in about {int(wait)}s"}
        try:
            data = resp.json()
        except ValueError:
            return {"error": f"Slack answered HTTP {resp.status_code} with no JSON"}
        if not isinstance(data, dict):
            return {"error": "Slack answered with something that is not an object"}
        if not data.get("ok"):
            return {"error": explain(str(data.get("error") or f"HTTP {resp.status_code}"), data)}
        return {"ok": True, "data": data}
    return {"error": "Slack is rate-limiting this token"}


def _retry_after(resp: Any) -> float:
    try:
        return max(1.0, float(resp.headers.get("Retry-After", "1")))
    except (TypeError, ValueError):
        return 1.0


def _clean(values: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Drop what the model left out. `False` and `0` are answers, so only None and "" go."""
    return {k: v for k, v in (values or {}).items() if v is not None and v != ""}
