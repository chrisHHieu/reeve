"""Who and what is in the Slack workspace, for the pickers in the Slack settings.

With these a person finds their own name in a list and picks channels by name, instead of a
message parked until someone approves it or an id pasted from Slack. They are reads on scopes every
install already has (`users:read`, `channels:read`, `groups:read`), so no new consent, and the lists
stay in this process's memory, not in the secret store: names and ids route messages, they are not
anyone's content.

Slack limits `users.list` to about 20 calls a minute and asks that it be cached, so each list is
fetched whole once per `_TTL` and searched here. A private channel is listed only where the bot is a
member, which is Slack's rule; a public one says `is_member`, so the picker can tell the person to
invite @Reeve rather than fail without a word.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from harness.safety.secrets import SecretStore

_TTL = 900.0  # a workspace's people change slowly; the picker's refresh fetches again at once
_MEMBERS_PER_PAGE = 200  # Slack's advice for users.list
# conversations.list takes up to 1000 a page, and the first fetch is time the person sits through:
# a large workspace took about 11 s at 200 a page.
_CHANNELS_PER_PAGE = 999
_PAGE_LIMIT = 25  # either list; past it, the person narrows the search
_NOT_A_PERSON = "USLACKBOT"

# kind -> (when fetched, rows). In memory for the life of the process and never written to disk.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _api_base() -> str:
    return os.environ.get("SLACK_API_URL", "https://slack.com/api/")


def _bot_token(secrets: SecretStore) -> str:
    """The bot token of the Socket Mode connection."""
    return str((secrets.get("slack:default") or {}).get("bot_token") or "")


def _get_pages(token: str, method: str, params: dict[str, Any], key: str, page_limit: int = _MEMBERS_PER_PAGE) -> list[dict]:
    """`key` from every page of a GET that pages by cursor. Slack's own error is raised as a
    `RuntimeError`."""
    import httpx

    collected: list[dict] = []
    cursor = ""
    for _page in range(_PAGE_LIMIT):
        query = {**params, "limit": page_limit}
        if cursor:
            query["cursor"] = cursor
        answer = httpx.get(_api_base() + method, params=query, headers={"Authorization": f"Bearer {token}"}, timeout=30.0).json()
        if not answer.get("ok"):
            raise RuntimeError(str(answer.get("error") or f"{method} failed"))
        collected += answer.get(key) or []
        cursor = (answer.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return collected


def _cached(kind: str, fetch: Callable[[], list[dict[str, Any]]], refresh: bool) -> list[dict[str, Any]]:
    """The list of `kind`, fetched again when asked to or when it is older than `_TTL`."""
    now = time.time()
    fetched_at, rows = _CACHE.get(kind, (0.0, None))
    if rows is None or refresh or now - fetched_at >= _TTL:
        rows = fetch()
        _CACHE[kind] = (now, rows)
    return rows


def _search(rows: list[dict], query: str, key: str, limit: int) -> list[dict]:
    """Rows whose `key` or handle holds `query`: names that start with it first, then the rest, each
    in alphabetical order; at most `limit` of them, kept between 1 and 100."""
    wanted = query.strip().lower()
    if wanted:
        rows = [row for row in rows if wanted in row[key].lower() or wanted in row.get("handle", "").lower()]
    ordered = sorted(rows, key=lambda row: (not row[key].lower().startswith(wanted), row[key].lower()))
    return ordered[: max(1, min(int(limit or 25), 100))]


def _person(member: dict) -> dict[str, Any]:
    profile = member.get("profile") or {}
    handle = member.get("name") or ""
    return {
        "id": member.get("id", ""),
        "name": profile.get("display_name") or profile.get("real_name") or handle,
        "handle": handle,
        "guest": bool(member.get("is_restricted") or member.get("is_ultra_restricted")),
    }


def _listing(secrets: SecretStore, kind: str, fetch: Callable[[str], list[dict]], query: str, limit: int, refresh: bool) -> dict[str, Any]:
    token = _bot_token(secrets)
    if not token:
        return {"ok": False, "error": "workspace not connected"}
    try:
        rows = _cached(kind, lambda: fetch(token), refresh)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, kind: _search(rows, query, "name", limit)}


def list_members(secrets: SecretStore, query: str = "", limit: int = 25, *, refresh: bool = False) -> dict[str, Any]:
    """The workspace's people: id, the name they go by, handle, and whether they are a guest. Bots,
    deactivated accounts and Slackbot are not listed; nobody needs to allow them."""

    def fetch(token: str) -> list[dict]:
        everyone = _get_pages(token, "users.list", {}, "members")
        return [_person(m) for m in everyone if not (m.get("deleted") or m.get("is_bot") or m.get("id") == _NOT_A_PERSON)]

    return _listing(secrets, "members", fetch, query, limit, refresh)


def list_channels(secrets: SecretStore, query: str = "", limit: int = 25, *, refresh: bool = False) -> dict[str, Any]:
    """The channels the bot can see: all the public ones, and the private ones it is in. With
    `is_member` the app can suggest inviting @Reeve to the others."""

    def fetch(token: str) -> list[dict]:
        params = {"types": "public_channel,private_channel", "exclude_archived": "true"}
        found = _get_pages(token, "conversations.list", params, "channels", page_limit=_CHANNELS_PER_PAGE)
        return [
            {"id": c["id"], "name": c["name"], "is_private": bool(c.get("is_private")), "is_member": bool(c.get("is_member"))}
            for c in found
            if c.get("id") and c.get("name")
        ]

    return _listing(secrets, "channels", fetch, query, limit, refresh)


def clear_cache() -> None:
    """Drop the cached lists, as a disconnect or reconnect needs."""
    _CACHE.clear()
