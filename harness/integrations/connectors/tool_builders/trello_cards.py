"""Trello's card tools: create or copy a card, change it, comment, checklists, attach a
file or a link, and set a custom field. Each is a write, so each asks before it runs (unless the
session's permission mode says otherwise), and the approval card shows exactly what changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .. import session_files
from .. import trello_api as api
from .trello import CARD_ID, _tool

#: Trello's own ceiling on one uploaded file (Standard and above; Free allows 10 MB and says so).
MAX_UPLOAD = 250 * 1024 * 1024

_DATE = {"type": "string", "description": "UTC ISO 8601 (2026-11-03T09:00:00Z); an empty string clears it."}
_IDS = {"type": "array", "items": {"type": "string"}}


def _unset(value: Any) -> Any:
    """An empty string clears a date: Trello takes null for that."""
    return None if value == "" else value


def trello_card_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def trello_create_card(
        list_id: str, name: str = "", desc: str = "", due: str = "", start: str = "", position: str = "bottom",
        label_ids: Optional[list[str]] = None, member_ids: Optional[list[str]] = None, copy_from_card_id: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        if not (name or copy_from_card_id):
            return {"error": "a card needs a name (or copy_from_card_id to copy one)"}
        body: dict[str, Any] = {"idList": list_id, "pos": position or "bottom"}
        for key, value in (("name", name), ("desc", desc), ("due", due), ("start", start)):
            if value:
                body[key] = value
        if label_ids:
            body["idLabels"] = api.csv(label_ids)
        if member_ids:
            body["idMembers"] = api.csv(member_ids)
        if copy_from_card_id:
            # Everything the source card has comes along: checklists, labels, members, attachments,
            # comments, due date and stickers.
            body["idCardSource"] = api.ref(copy_from_card_id)
            body["keepFromSource"] = "all"
        result = api.call(secrets, account, "POST", "/cards", json=body)
        card = api.data(result)
        return result if card is None else {**{k: v for k, v in result.items() if k != "data"}, "card": api.card_row(card)}

    _tool(tools, trello_create_card, "trello_create_card",
          "Create a card in a list, or copy one (copy_from_card_id keeps its checklists, labels, members and "
          "attachments). Ids come from trello_get_board.",
          {"list_id": {"type": "string"}, "name": {"type": "string"}, "desc": {"type": "string", "description": "Markdown."},
           "due": _DATE, "start": _DATE, "position": {"type": "string", "description": "top, bottom, or a number."},
           "label_ids": _IDS, "member_ids": _IDS, "copy_from_card_id": CARD_ID}, ["list_id"], "write")

    def trello_update_card(
        card_id: str, name: Optional[str] = None, desc: Optional[str] = None, due: Optional[str] = None,
        start: Optional[str] = None, done: Optional[bool] = None, list_id: str = "", board_id: str = "",
        position: str = "", archived: Optional[bool] = None, add_label_ids: Optional[list[str]] = None,
        remove_label_ids: Optional[list[str]] = None, add_member_ids: Optional[list[str]] = None,
        remove_member_ids: Optional[list[str]] = None, cover_color: Optional[str] = None, account: str = "",
    ) -> dict[str, Any]:
        card = api.ref(card_id)
        body: dict[str, Any] = {}
        for key, value in (("name", name), ("desc", desc), ("dueComplete", done), ("closed", archived)):
            if value is not None:
                body[key] = value
        for key, value in (("due", due), ("start", start)):
            if value is not None:
                body[key] = _unset(value)
        if list_id:
            body["idList"] = list_id
        if board_id:
            body["idBoard"] = api.ref(board_id)
        if position:
            body["pos"] = position
        if cover_color is not None:
            body["cover"] = {"color": cover_color or None}
        changed: list[str] = sorted(body)
        result: dict[str, Any] = {"ok": True}
        if body:
            result = api.call(secrets, account, "PUT", f"/cards/{card}", json=body)
            if result.get("error"):
                return result
        # Labels and members are added and taken away one at a time, so a change never rewrites
        # the ones the model did not mention.
        steps = [("POST", "idLabels", i, "added label") for i in add_label_ids or []]
        steps += [("DELETE", "idLabels", i, "removed label") for i in remove_label_ids or []]
        steps += [("POST", "idMembers", i, "added member") for i in add_member_ids or []]
        steps += [("DELETE", "idMembers", i, "removed member") for i in remove_member_ids or []]
        for method, field, value, said in steps:
            step = api.call(secrets, account, method, f"/cards/{card}/{field}" + (f"/{value}" if method == "DELETE" else ""),
                            json={"value": value} if method == "POST" else None)
            if step.get("error"):
                return {**step, "done_before_failing": changed}
            changed.append(f"{said} {value}")
        if not changed:
            return {"error": "nothing to change: name at least one field"}
        fresh = api.data(api.call(secrets, account, "GET", f"/cards/{card}", params={"fields": api.CARD_FIELDS + ",shortUrl,labels"}))
        return {**{k: v for k, v in result.items() if k != "data"}, "changed": changed, **({"card": api.card_row(fresh)} if fresh else {})}

    _tool(tools, trello_update_card, "trello_update_card",
          "Change a card: name, description, dates, done, move it (list_id, board_id, position), archive or restore it, "
          "add or remove labels and members, or its cover colour. Only the fields given change.",
          {"card_id": CARD_ID, "name": {"type": "string"}, "desc": {"type": "string"}, "due": _DATE, "start": _DATE,
           "done": {"type": "boolean", "description": "Marks the due date complete."},
           "list_id": {"type": "string"}, "board_id": {"type": "string"}, "position": {"type": "string"},
           "archived": {"type": "boolean"}, "add_label_ids": _IDS, "remove_label_ids": _IDS,
           "add_member_ids": _IDS, "remove_member_ids": _IDS,
           "cover_color": {"type": "string", "description": "green, yellow, orange, red, purple, blue, sky, lime, pink, black; empty removes it."}},
          ["card_id"], "write")

    def trello_comment(card_id: str, text: str, comment_id: str = "", account: str = "") -> dict[str, Any]:
        if not text.strip():
            return {"error": "a comment needs text"}
        if comment_id:
            result = api.call(secrets, account, "PUT", f"/actions/{comment_id}", json={"text": text})
        else:
            result = api.call(secrets, account, "POST", f"/cards/{api.ref(card_id)}/actions/comments", json={"text": text})
        action = api.data(result)
        return result if action is None else {**{k: v for k, v in result.items() if k != "data"}, "comment": api.comment_row(action)}

    _tool(tools, trello_comment, "trello_comment",
          "Comment on a card (Markdown; @username mentions notify). With comment_id, edit that comment instead.",
          {"card_id": CARD_ID, "text": {"type": "string"}, "comment_id": {"type": "string"}}, ["card_id", "text"], "write")

    def trello_checklist(
        action: str, card_id: str = "", checklist_id: str = "", item_id: str = "", name: str = "",
        items: Optional[list[str]] = None, done: Optional[bool] = None, due: Optional[str] = None,
        member_id: Optional[str] = None, position: str = "", account: str = "",
    ) -> dict[str, Any]:
        if action == "create":
            if not (card_id and name):
                return {"error": "create needs card_id and name"}
            made = api.call(secrets, account, "POST", "/checklists", json={"idCard": api.ref(card_id), "name": name, "pos": position or "bottom"})
            checklist = api.data(made)
            if checklist is None:
                return made
            added = []
            for index, text in enumerate(items or [], start=1):
                step = api.call(secrets, account, "POST", f"/checklists/{checklist['id']}/checkItems", json={"name": text, "pos": index})
                if step.get("error"):
                    return {**step, "checklist_id": checklist["id"], "items_added": added}
                added.append({"id": (api.data(step) or {}).get("id"), "name": text})
            return {"ok": True, "checklist_id": checklist["id"], "items": added}
        if action == "add_item":
            if not (checklist_id and name):
                return {"error": "add_item needs checklist_id and name"}
            body: dict[str, Any] = {"name": name, "pos": position or "bottom"}
            if done:
                body["checked"] = True
            if due:
                body["due"] = due
            if member_id:
                body["idMember"] = member_id
            result = api.call(secrets, account, "POST", f"/checklists/{checklist_id}/checkItems", json=body)
        elif action == "update_item":
            if not (card_id and item_id):
                return {"error": "update_item needs card_id and item_id"}
            body = {}
            if done is not None:
                body["state"] = "complete" if done else "incomplete"
            if name:
                body["name"] = name
            if due is not None:
                body["due"] = _unset(due)
            if member_id is not None:
                body["idMember"] = member_id or None
            if position:
                body["pos"] = position
            if not body:
                return {"error": "nothing to change on the item"}
            result = api.call(secrets, account, "PUT", f"/cards/{api.ref(card_id)}/checkItem/{item_id}", json=body)
        elif action == "rename":
            if not (checklist_id and name):
                return {"error": "rename needs checklist_id and name"}
            result = api.call(secrets, account, "PUT", f"/checklists/{checklist_id}", json={"name": name})
        else:
            return {"error": "action is one of create, add_item, update_item, rename"}
        return {k: v for k, v in result.items() if k != "data"} if result.get("ok") else result

    _tool(tools, trello_checklist, "trello_checklist",
          "Checklists on a card. action=create (card_id, name, items: the items in order), add_item (checklist_id, "
          "name, due, member_id), update_item (card_id, item_id: tick or untick with done, rename, due, member_id), "
          "rename (checklist_id, name). Ids come from trello_get_card.",
          {"action": {"type": "string", "enum": ["create", "add_item", "update_item", "rename"]}, "card_id": CARD_ID,
           "checklist_id": {"type": "string"}, "item_id": {"type": "string"}, "name": {"type": "string"},
           "items": _IDS, "done": {"type": "boolean"}, "due": _DATE, "member_id": {"type": "string"},
           "position": {"type": "string"}}, ["action"], "write")

    def trello_attach(card_id: str, path: str = "", url: str = "", name: str = "", make_cover: bool = False, account: str = "") -> dict[str, Any]:
        if bool(path) == bool(url):
            return {"error": "give either path (a file in the session folder) or url (a link), not both"}
        aid, profile, err = api.profile_for(secrets, account)
        if err:
            return err
        endpoint = f"{api.BASE}/cards/{api.ref(card_id)}/attachments"
        if url:
            result = api.call(secrets, account, "POST", f"/cards/{api.ref(card_id)}/attachments",
                              json={"url": url, "name": name or None, "setCover": make_cover})
        else:
            file, refusal = session_files.outgoing(path, roots, remote=remote, limit=MAX_UPLOAD, service="Trello")
            if refusal:
                return {"error": refusal}
            result = api_upload(endpoint, profile, file, name or file.name, make_cover)
        attached = api.data(result)
        if attached is None:
            return result
        return {"ok": True, **({"account": aid} if aid else {}), "attachment": {"id": attached.get("id"), "name": attached.get("name")}}

    _tool(tools, trello_attach, "trello_attach",
          "Attach a file from the session folder (path) or a link (url) to a card. make_cover shows an image as the cover.",
          {"card_id": CARD_ID, "path": {"type": "string"}, "url": {"type": "string"}, "name": {"type": "string"},
           "make_cover": {"type": "boolean"}}, ["card_id"], "write")

    def trello_set_custom_field(card_id: str, field_id: str, value: str = "", option_id: str = "", account: str = "") -> dict[str, Any]:
        board = api.data(api.call(secrets, account, "GET", f"/cards/{api.ref(card_id)}", params={"fields": "idBoard"}))
        if board is None:
            return {"error": f"no card {card_id!r}"}
        fields = api.data(api.call(secrets, account, "GET", f"/boards/{board['idBoard']}/customFields")) or []
        field = next((f for f in fields if f.get("id") == field_id), None)
        if field is None:
            return {"error": f"no custom field {field_id!r} on this card's board; trello_get_board lists them"}
        kind = field.get("type")
        if option_id or kind == "list":
            body: dict[str, Any] = {"idValue": option_id} if option_id else {"idValue": "", "value": ""}
        elif value == "":
            body = {"value": ""}  # clears it
        elif kind == "checkbox":
            body = {"value": {"checked": "true" if value.lower() in ("true", "yes", "1", "checked") else "false"}}
        else:
            body = {"value": {kind: value}}
        result = api.call(secrets, account, "PUT", f"/cards/{api.ref(card_id)}/customField/{field_id}/item", json=body)
        return {k: v for k, v in result.items() if k != "data"} if result.get("ok") else result

    _tool(tools, trello_set_custom_field, "trello_set_custom_field",
          "Set a card's custom field: value for text, number, date (UTC ISO 8601) or checkbox (true/false); option_id "
          "for a dropdown. An empty value clears it. Field and option ids come from trello_get_board.",
          {"card_id": CARD_ID, "field_id": {"type": "string"}, "value": {"type": "string"}, "option_id": {"type": "string"}},
          ["card_id", "field_id"], "write")
    return tools


def api_upload(endpoint: str, profile: dict[str, Any], file: Path, name: str, make_cover: bool) -> dict[str, Any]:
    """One multipart upload; a module function so a test can stand in for the network."""
    try:
        import httpx

        with httpx.Client(timeout=300.0) as client, file.open("rb") as handle:
            response = client.post(
                endpoint, headers=api.auth_header(profile),
                data={"name": name, "setCover": "true" if make_cover else "false"},
                files={"file": (name, handle)},
            )
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "details": response.text[:500]}
        return {"ok": True, "data": response.json()}
    except Exception as exc:
        return {"error": f"the upload failed: {exc.__class__.__name__}"}
