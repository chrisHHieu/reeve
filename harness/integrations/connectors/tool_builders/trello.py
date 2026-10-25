"""Trello's read tools: who the account is, its boards, a board's shape, cards, one card
whole, search, history, and saving an attachment into the session folder.

Each read answers what the next write needs: a board read names every list, label, member and
custom field with its id, so a card can be moved, labelled, assigned or filled in without guessing.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .. import session_files
from .. import trello_api as api
from ..profiles import GENERIC_ACCOUNT_PROP
from ..toolkit import attach, clamp, function_schema

#: The largest attachment saved into the session folder.
MAX_DOWNLOAD = 50 * 1024 * 1024
#: Hosts an uploaded attachment is served from; anything else is a link, not a file.
_ATTACHMENT_HOSTS = ("https://trello.com/", "https://api.trello.com/", "https://trello-attachments.s3.amazonaws.com/")

ACCOUNT = {"account": GENERIC_ACCOUNT_PROP}
CARD_ID = {"type": "string", "description": "The card's id, short link, or trello.com/c/… URL."}
BOARD_ID = {"type": "string", "description": "The board's id, short link, or trello.com/b/… URL."}


def _tool(tools: list, fn: Callable[..., Any], name: str, description: str, props: dict, required: list[str], kind: str) -> None:
    tools.append(attach(fn, function_schema(name, description, {**props, **ACCOUNT}, required), caps=["trello", kind]))


def trello_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def trello_whoami(account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", "/members/me", params={"fields": "username,fullName,url,initials"})
        me = api.data(result)
        if me is None:
            return result
        return {**{k: v for k, v in result.items() if k != "data"}, "id": me.get("id"), "username": me.get("username"),
                "name": me.get("fullName"), "url": me.get("url"),
                "dates": "Trello stores due and start dates in UTC (ISO 8601): convert a local time before writing one."}

    _tool(tools, trello_whoami, "trello_whoami", "The Trello account this connection acts as: its id (for assigning cards to yourself), username and name.", {}, [], "read")

    def trello_list_boards(query: str = "", include_closed: bool = False, account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", "/members/me/boards", params={
            "filter": "all" if include_closed else "open", "fields": "name,url,closed,idOrganization,dateLastActivity",
            "organization": "true", "organization_fields": "displayName",
        })
        boards = api.data(result)
        if boards is None:
            return result
        wanted = query.strip().lower()
        rows = []
        for board in boards:
            if wanted and wanted not in str(board.get("name", "")).lower():
                continue
            row = {"id": board.get("id"), "name": board.get("name"), "url": board.get("url"),
                   "workspace": (board.get("organization") or {}).get("displayName") or "Personal"}
            if board.get("closed"):
                row["closed"] = True
            rows.append(row)
        rows.sort(key=lambda r: (r["workspace"], str(r["name"]).lower()))
        return {"ok": True, **({"account": result["account"]} if "account" in result else {}), "boards": rows}

    _tool(tools, trello_list_boards, "trello_list_boards",
          "List the Trello boards this account can open, with their workspace. `query` filters by name.",
          {"query": {"type": "string"}, "include_closed": {"type": "boolean"}}, [], "read")

    def trello_get_board(board_id: str, account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", f"/boards/{api.ref(board_id)}", params={
            "fields": "name,desc,url,closed,idOrganization", "lists": "open", "list_fields": "name,pos,closed",
            "labels": "all", "label_fields": "name,color", "labels_limit": 1000,
            "members": "all", "member_fields": "username,fullName", "customFields": "true",
        })
        board = api.data(result)
        if board is None:
            return result
        out = {k: v for k, v in result.items() if k != "data"}
        out.update({
            "id": board.get("id"), "name": board.get("name"), "url": board.get("url"), "desc": board.get("desc") or "",
            "lists": [{"id": li.get("id"), "name": li.get("name")} for li in board.get("lists") or []],
            "labels": [{"id": la.get("id"), "name": la.get("name") or "", "color": la.get("color")} for la in board.get("labels") or []],
            "members": [{"id": m.get("id"), "username": m.get("username"), "name": m.get("fullName")} for m in board.get("members") or []],
        })
        fields = board.get("customFields") or []
        if fields:
            out["custom_fields"] = [
                {"id": f.get("id"), "name": f.get("name"), "type": f.get("type"),
                 **({"options": [{"id": o.get("id"), "text": (o.get("value") or {}).get("text")} for o in f["options"]]} if f.get("options") else {})}
                for f in fields
            ]
        if board.get("closed"):
            out["closed"] = True
        return out

    _tool(tools, trello_get_board, "trello_get_board",
          "Read a board: its lists, labels, members and custom fields, each with the id a write needs.",
          {"board_id": BOARD_ID}, ["board_id"], "read")

    def trello_list_cards(
        board_id: str = "", list_id: str = "", mine: bool = False, due_within_days: int = 0,
        label: str = "", member_id: str = "", include_archived: bool = False, limit: int = 100, account: str = "",
    ) -> dict[str, Any]:
        if not (board_id or list_id or mine):
            return {"error": "name a board_id, a list_id, or set mine=true"}
        params = {"fields": api.CARD_FIELDS + ",desc,shortUrl,labels", "filter": "all" if include_archived else "open"}
        if list_id:
            path = f"/lists/{list_id}/cards"
        elif board_id:
            path = f"/boards/{api.ref(board_id)}/cards"
        else:
            path, params["filter"] = "/members/me/cards", "all" if include_archived else "visible"
        result = api.call(secrets, account, "GET", path, params=params)
        cards = api.data(result)
        if cards is None:
            return result
        if due_within_days:
            from datetime import datetime, timedelta, timezone

            until = datetime.now(timezone.utc) + timedelta(days=int(due_within_days))
            cards = [c for c in cards if c.get("due") and not c.get("dueComplete")
                     and datetime.fromisoformat(str(c["due"]).replace("Z", "+00:00")) <= until]
            cards.sort(key=lambda c: c["due"])
        if member_id:
            cards = [c for c in cards if member_id in (c.get("idMembers") or [])]
        if label:
            wanted = label.strip().lower()
            cards = [c for c in cards if any(wanted in (api.label_text(la).lower(), la.get("id")) for la in c.get("labels") or [])]
        shown = cards[: clamp(limit, default=100, ceiling=500)]
        return {"ok": True, **({"account": result["account"]} if "account" in result else {}),
                "cards": [api.card_row(c) for c in shown], **({"more": len(cards) - len(shown)} if len(cards) > len(shown) else {})}

    _tool(tools, trello_list_cards, "trello_list_cards",
          "List cards on a board, in a list, or assigned to you (mine=true). Filter by a due date within N days "
          "(overdue included, done excluded), a label name or id, or a member id.",
          {"board_id": BOARD_ID, "list_id": {"type": "string"}, "mine": {"type": "boolean"},
           "due_within_days": {"type": "integer"}, "label": {"type": "string"}, "member_id": {"type": "string"},
           "include_archived": {"type": "boolean"}, "limit": {"type": "integer"}}, [], "read")

    def trello_get_card(card_id: str, comments: int = 20, account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", f"/cards/{api.ref(card_id)}", params={
            "fields": api.CARD_FIELDS + ",desc,shortUrl,cover", "list": "true", "list_fields": "name",
            "board": "true", "board_fields": "name,url", "members": "true", "member_fields": "username,fullName",
            "checklists": "all", "checkItemStates": "true", "attachments": "true",
            "attachment_fields": "name,url,bytes,mimeType,isUpload,date", "customFieldItems": "true",
            "actions": "commentCard", "actions_limit": clamp(comments, default=20, ceiling=100),
        })
        card = api.data(result)
        if card is None:
            return result
        out = {k: v for k, v in result.items() if k != "data"}
        out.update(api.card_row(card))
        out["desc"] = card.get("desc") or ""
        out["list"] = {"id": card.get("idList"), "name": (card.get("list") or {}).get("name")}
        out["board"] = {"id": card.get("idBoard"), "name": (card.get("board") or {}).get("name")}
        out["labels"] = [{"id": la.get("id"), "name": api.label_text(la)} for la in card.get("labels") or []]
        out["members"] = [{"id": m.get("id"), "username": m.get("username")} for m in card.get("members") or []]
        out.pop("member_ids", None)
        out["checklists"] = [
            {"id": cl.get("id"), "name": cl.get("name"),
             "items": [{"id": i.get("id"), "name": i.get("name"), "done": i.get("state") == "complete",
                        **({"due": i["due"]} if i.get("due") else {}), **({"member_id": i["idMember"]} if i.get("idMember") else {})}
                       for i in sorted(cl.get("checkItems") or [], key=lambda i: i.get("pos", 0))]}
            for cl in card.get("checklists") or []
        ]
        out["attachments"] = [
            {"id": a.get("id"), "name": a.get("name"), "kind": "file" if a.get("isUpload") else "link",
             **({"bytes": a["bytes"]} if a.get("bytes") else {}), **({"url": a["url"]} if not a.get("isUpload") else {})}
            for a in card.get("attachments") or []
        ]
        out["comments"] = [api.comment_row(a) for a in card.get("actions") or []]
        items = card.get("customFieldItems") or []
        if items:
            board = api.data(api.call(secrets, account, "GET", f"/boards/{card.get('idBoard')}/customFields")) or []
            by_id = {f.get("id"): f for f in board}
            out["custom_fields"] = {
                (by_id.get(i.get("idCustomField")) or {}).get("name", i.get("idCustomField")): api.custom_value(i, by_id.get(i.get("idCustomField")))
                for i in items
            }
        return out

    _tool(tools, trello_get_card, "trello_get_card",
          "Read one card whole: description, list and board, dates, labels, members, checklists with their items, "
          "attachments, the latest comments (newest first) and custom field values.",
          {"card_id": CARD_ID, "comments": {"type": "integer", "description": "How many recent comments (default 20)."}},
          ["card_id"], "read")

    def trello_search(query: str, boards: int = 5, cards: int = 20, board_id: str = "", account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", "/search", params={
            "query": query, "modelTypes": "cards,boards", "partial": "true",
            "cards_limit": clamp(cards, default=20, ceiling=100), "boards_limit": clamp(boards, default=5, ceiling=50),
            "card_fields": api.CARD_FIELDS + ",desc,shortUrl", "card_board": "true", "card_list": "true",
            "board_fields": "name,url", "idBoards": api.ref(board_id) if board_id else None,
        })
        found = api.data(result)
        if found is None:
            return result
        rows = []
        for card in found.get("cards") or []:
            row = api.card_row(card)
            row["board"] = (card.get("board") or {}).get("name")
            row["list"] = (card.get("list") or {}).get("name")
            rows.append(row)
        return {"ok": True, **({"account": result["account"]} if "account" in result else {}), "cards": rows,
                "boards": [{"id": b.get("id"), "name": b.get("name"), "url": b.get("url")} for b in found.get("boards") or []]}

    _tool(tools, trello_search, "trello_search",
          "Search cards and boards. Trello's operators work in `query`: board:, list:, label:, member:me, "
          "due:week, due:overdue, is:open, has:attachments, created:7, edited:1.",
          {"query": {"type": "string"}, "boards": {"type": "integer"}, "cards": {"type": "integer"},
           "board_id": {**BOARD_ID, "description": "Only this board."}}, ["query"], "read")

    def trello_activity(board_id: str = "", card_id: str = "", kind: str = "", limit: int = 30, account: str = "") -> dict[str, Any]:
        if not (board_id or card_id):
            return {"error": "name a board_id or a card_id"}
        path = f"/cards/{api.ref(card_id)}/actions" if card_id else f"/boards/{api.ref(board_id)}/actions"
        result = api.call(secrets, account, "GET", path, params={
            "filter": kind or "all", "limit": clamp(limit, default=30, ceiling=200),
            "memberCreator_fields": "username,fullName",
        })
        actions = api.data(result)
        if actions is None:
            return result
        return {"ok": True, **({"account": result["account"]} if "account" in result else {}),
                "activity": [api.activity_row(a) for a in actions]}

    _tool(tools, trello_activity, "trello_activity",
          "What happened on a board or a card, newest first: who created, moved, edited or commented on which card. "
          "`kind` narrows it to Trello action types, comma-separated (createCard, updateCard, commentCard).",
          {"board_id": BOARD_ID, "card_id": CARD_ID, "kind": {"type": "string"}, "limit": {"type": "integer"}}, [], "read")

    def trello_download_attachment(card_id: str, attachment_id: str, account: str = "") -> dict[str, Any]:
        result = api.call(secrets, account, "GET", f"/cards/{api.ref(card_id)}/attachments/{attachment_id}")
        meta = api.data(result)
        if meta is None:
            return result
        if not meta.get("isUpload") or not str(meta.get("url", "")).startswith(_ATTACHMENT_HOSTS):
            return {"error": "this attachment is a link, not a file kept on Trello", "url": meta.get("url")}
        if int(meta.get("bytes") or 0) > MAX_DOWNLOAD:
            return {"error": f"the file is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB"}
        _aid, profile, err = api.profile_for(secrets, account)
        if err:
            return err
        payload, failure = api_download(meta["url"], profile)
        if failure:
            return {"error": failure}
        target, refusal = session_files.landing(meta.get("name") or f"attachment-{attachment_id[:8]}", roots, remote=remote)
        if refusal:
            return {"error": refusal}
        target.write_bytes(payload)
        return {"ok": True, **({"account": result["account"]} if "account" in result else {}), "path": str(target), "size": len(payload)}

    _tool(tools, trello_download_attachment, "trello_download_attachment",
          "Save one of a card's file attachments into the session folder. attachment_id comes from trello_get_card.",
          {"card_id": CARD_ID, "attachment_id": {"type": "string"}}, ["card_id", "attachment_id"], "write")
    return tools


def api_download(url: str, profile: dict[str, Any]) -> tuple[bytes, str]:
    """(bytes, "") or (b"", why). Trello serves an uploaded file only to an authorized request;
    a module function so a test can stand in for the network."""
    try:
        import httpx

        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(url, headers=api.auth_header(profile))
        if response.status_code >= 400:
            return b"", f"Trello did not hand over the file (HTTP {response.status_code})"
        if len(response.content) > MAX_DOWNLOAD:
            return b"", f"the file is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB"
        return response.content, ""
    except Exception as exc:
        return b"", f"the download failed: {exc.__class__.__name__}"
