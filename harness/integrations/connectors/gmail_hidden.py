""""Never show agents", applied to what a Gmail tool is about to return.

The filter (senders and labels the person keeps from the agent) was enforced by search and by
reading one message, and nowhere else: a thread read returned every message in it, a thread
search returned each thread's snippet (the newest message's first words), a draft and an
attachment could be fetched by id. Each of those is a way to read the same mail.

One rule for all of them: what is hidden is indistinguishable from what is missing. A hidden
message is dropped from a list without a tombstone, and asked for by id it is the same 404 a
message that never existed gets — a difference between the two would let the agent probe for it.
"""

from __future__ import annotations

from email.utils import parseaddr
from typing import Any, Callable, Optional

from harness.safety.secrets import SecretStore

from . import calls, gmail_accounts
from .headers import google_headers

#: What a message that is not there answers — and so what a hidden one answers.
NOT_FOUND: dict[str, Any] = {"error": "HTTP 404", "details": {"error": {"code": 404, "message": "Not Found"}}}


def gmail_filters(secrets: SecretStore) -> Optional[dict[str, list[str]]]:
    """The senders and labels the person keeps from the agent, or None when there are none."""
    filters = gmail_accounts.get_filters(secrets)
    return filters if (filters["senders"] or filters["labels"]) else None


def _from_address(message: dict[str, Any]) -> str:
    for header in (message.get("payload") or {}).get("headers") or []:
        if str(header.get("name", "")).lower() == "from":
            return parseaddr(str(header.get("value") or ""))[1]
    return ""


def gmail_label_map(token: str) -> dict[str, str]:
    """Label id -> label name for the mailbox. People filter on names, Gmail reports ids."""
    response = calls.request(
        "GET", "https://gmail.googleapis.com/gmail/v1/users/me/labels", headers=google_headers(token)
    )
    if not response.get("ok"):
        return {}
    labels = (response.get("data") or {}).get("labels") or []
    return {str(label.get("id") or ""): str(label.get("name") or "") for label in labels}


def gmail_is_hidden(message: dict[str, Any], filters: dict[str, list[str]], label_map: dict[str, str]) -> bool:
    """Whether a message matches a filtered sender or carries a filtered label (by name or id)."""
    if filters["senders"] and gmail_accounts.sender_matches(_from_address(message), filters["senders"]):
        return True
    wanted = {name.lower() for name in filters["labels"]}
    if wanted:
        for label_id in message.get("labelIds") or []:
            if label_map.get(str(label_id), "").lower() in wanted or str(label_id).lower() in wanted:
                return True
    return False


def not_found(hidden: int = 1) -> dict[str, Any]:
    return {**NOT_FOUND, "_display": {"hidden_by_filters": hidden, "connector": "gmail"}}


def checker(secrets: SecretStore, token: str) -> Optional[Callable[[dict[str, Any]], bool]]:
    """A test for "is this message hidden", or None when nothing is filtered.

    None is the common case and costs nothing: no label lookup, no per-message fetch. The
    message it is given needs `labelIds` and a `From` header (a metadata fetch has both).
    """
    filters = gmail_filters(secrets)
    if not filters:
        return None
    labels = gmail_label_map(token) if filters["labels"] else {}
    return lambda message: gmail_is_hidden(message, filters, labels)


def thread_listing(result: dict[str, Any], is_hidden: Callable, fetch_thread: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """A `threads.list` result with hidden threads dropped.

    A thread's snippet is its newest message's first words, so a listing leaks the mail it hides.
    Each thread is checked message by message (`fetch_thread(id)` returns it with headers): all
    hidden -> omitted; the newest hidden -> kept without its snippet.
    """
    data = dict(result.get("data") or {})
    kept, hidden = [], 0
    for thread in data.get("threads") or []:
        detail = fetch_thread(str(thread.get("id")))
        messages = ((detail.get("data") or {}).get("messages") or []) if detail.get("ok") else []
        flags = [is_hidden(m) for m in messages]
        if messages and all(flags):
            hidden += 1
            continue
        if not messages or flags[-1]:  # newest is hidden, or could not be checked: an id reveals nothing, a snippet does
            thread = {k: v for k, v in thread.items() if k != "snippet"}
        kept.append(thread)
    data["threads"] = kept
    out = {**result, "data": data}
    if hidden:
        out["_display"] = {"hidden_by_filters": hidden, "connector": "gmail"}
    return out


def one_thread(result: dict[str, Any], is_hidden: Callable) -> dict[str, Any]:
    """A `threads.get` result without its hidden messages; a thread with none left is a 404."""
    data = dict(result.get("data") or {})
    messages = data.get("messages") or []
    kept = [m for m in messages if not is_hidden(m)]
    if not kept:
        return not_found(len(messages))
    if len(kept) == len(messages):
        return result
    data["messages"] = kept
    return {**result, "data": data, "_display": {"hidden_by_filters": len(messages) - len(kept), "connector": "gmail"}}
