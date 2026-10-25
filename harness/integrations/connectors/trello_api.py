"""Trello's REST API as the Trello tools reach it: the call, the ids, and what a card reads as.

The tools are ours, not Trello's MCP server's. That server groups many actions under one
tool (`trelloWriteCard` creates, edits and archives), which a per-tool approval cannot tell apart,
and it lacks comments, attachments, custom fields, labels and members as of 2026-10. The REST API
(v1, stable for a decade) has all of them.

Credentials are the app's API key and the person's token. They ride in the `Authorization` header
Trello documents for this (`OAuth oauth_consumer_key=…, oauth_token=…`), never in the query
string, so no URL that reaches a log or an error message carries the token.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import calls
from .profiles import account_profile, account_result

BASE = "https://api.trello.com/1"
NAME = "trello"

#: What the read tools return of a card in a list; `trello_get_card` returns the rest.
CARD_FIELDS = "name,idList,idBoard,url,shortLink,due,start,dueComplete,closed,idLabels,idMembers,pos,dateLastActivity"
#: The longest description a card list shows before it is cut (a card read shows it whole).
DESC_IN_LIST = 280

_SHORT = re.compile(r"trello\.com/[bc]/([A-Za-z0-9]+)")


def auth_header(profile: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f'OAuth oauth_consumer_key="{profile["api_key"]}", oauth_token="{profile["token"]}"'}


def ref(value: str) -> str:
    """A card's or board's id from what the model wrote: an id, a short link, or a Trello URL.
    Trello takes a short link wherever it takes a card or board id."""
    text = str(value or "").strip()
    found = _SHORT.search(text)
    return found.group(1) if found else text.strip("/")


def profile_for(secrets: Any, account: str = "") -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    return account_profile(secrets, NAME, account, "api_key", "token")


def call(
    secrets: Any,
    account: str,
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One API call for the account asked for (or the default one), with the account named in
    the result once more than one is connected. `{"ok", "data"}` or `{"error"}`; never raises."""
    aid, profile, err = profile_for(secrets, account)
    if err:
        return err
    clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    result = calls.request(method, f"{BASE}{path}", headers=auth_header(profile), params=clean or None, json=json)
    return account_result(aid, _explain(result))


def _explain(result: dict[str, Any]) -> dict[str, Any]:
    """Trello answers a refusal with a short text body ("invalid id", "unauthorized permission
    requested"); it goes into the error so the model can act on it."""
    if not result.get("error"):
        return result
    details = result.get("details")
    status = str(result.get("error"))
    if status == "HTTP 401":
        return {"error": "Trello refused the token (HTTP 401): reconnect Trello in Settings ▸ Connectors.", "details": details}
    if status == "HTTP 429":
        return {"error": "Trello's rate limit was hit (HTTP 429): wait about ten seconds and try again.", "details": details}
    return result


def data(result: dict[str, Any]) -> Any:
    return result.get("data") if result.get("ok") else None


# -- what an object reads as -----------------------------------------------------------------


def label_text(label: dict[str, Any]) -> str:
    """A label as a person names it: its name, else its colour."""
    return str(label.get("name") or label.get("color") or "no colour")


def card_row(card: dict[str, Any], *, lists: Optional[dict[str, str]] = None, labels: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """One card in a list of cards: what it is, where it is, and when it is due."""
    row: dict[str, Any] = {"id": card.get("id"), "name": card.get("name"), "url": card.get("shortUrl") or card.get("url")}
    if lists and card.get("idList") in lists:
        row["list"] = lists[card["idList"]]
    for key in ("due", "start"):
        if card.get(key):
            row[key] = card[key]
    if card.get("dueComplete"):
        row["done"] = True
    if card.get("closed"):
        row["archived"] = True
    if card.get("labels"):
        row["labels"] = [label_text(label) for label in card["labels"]]
    elif labels and card.get("idLabels"):
        row["labels"] = [labels.get(i, i) for i in card["idLabels"]]
    if card.get("idMembers"):
        row["member_ids"] = card["idMembers"]
    desc = str(card.get("desc") or "")
    if desc:
        row["desc"] = desc if len(desc) <= DESC_IN_LIST else desc[:DESC_IN_LIST] + "…"
    return row


def comment_row(action: dict[str, Any]) -> dict[str, Any]:
    member = action.get("memberCreator") or {}
    return {
        "id": action.get("id"),
        "by": member.get("username") or member.get("fullName") or action.get("idMemberCreator"),
        "date": action.get("date"),
        "text": (action.get("data") or {}).get("text", ""),
    }


def activity_row(action: dict[str, Any]) -> dict[str, Any]:
    """One entry of a board's or card's history: who did what to which card, when."""
    member = action.get("memberCreator") or {}
    info = action.get("data") or {}
    row: dict[str, Any] = {
        "type": action.get("type"),
        "by": member.get("username") or member.get("fullName"),
        "date": action.get("date"),
    }
    if info.get("card"):
        row["card"] = {"id": info["card"].get("id"), "name": info["card"].get("name")}
    if info.get("listBefore") and info.get("listAfter"):
        row["moved"] = f"{info['listBefore'].get('name')} → {info['listAfter'].get('name')}"
    elif info.get("list"):
        row["list"] = info["list"].get("name")
    if info.get("text"):
        row["text"] = info["text"]
    if info.get("old"):
        row["changed"] = sorted(info["old"])
    return row


def custom_value(item: dict[str, Any], field: Optional[dict[str, Any]]) -> Any:
    """A card's value for one custom field, as the person sees it: the option's text for a
    dropdown, the bare value otherwise."""
    if item.get("idValue") and field:
        for option in field.get("options") or []:
            if option.get("id") == item["idValue"]:
                return (option.get("value") or {}).get("text")
    value = item.get("value") or {}
    for kind in ("text", "number", "date", "checked"):
        if kind in value:
            return value[kind] == "true" if kind == "checked" else value[kind]
    return None


def csv(ids: Optional[list[str]]) -> Optional[str]:
    """A list of ids as Trello's comma-separated parameter; None leaves the field untouched."""
    if ids is None:
        return None
    return ",".join(str(i).strip() for i in ids if str(i).strip())
