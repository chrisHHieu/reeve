"""Trello's board tools: lists, labels, boards, and the one tool that deletes.

Deleting is a tool of its own so the approval card says plainly that something goes for good; every
other tool archives, which can be undone from Trello.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .. import trello_api as api
from .trello import BOARD_ID, _tool

_COLORS = "green, yellow, orange, red, purple, blue, sky, lime, pink, black (or a _light/_dark variant); empty is no colour"


def _bare(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {**{k: v for k, v in result.items() if k != "data"}, **extra} if result.get("ok") else result


def trello_board_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def trello_list(
        action: str, board_id: str = "", list_id: str = "", name: str = "", position: str = "",
        to_board_id: str = "", to_list_id: str = "", account: str = "",
    ) -> dict[str, Any]:
        if action == "create":
            if not (board_id and name):
                return {"error": "create needs board_id and name"}
            made = api.call(secrets, account, "POST", "/lists", json={"idBoard": api.ref(board_id), "name": name, "pos": position or "bottom"})
            listed = api.data(made)
            return made if listed is None else _bare(made, list={"id": listed.get("id"), "name": listed.get("name")})
        if not list_id:
            return {"error": f"{action} needs list_id"}
        if action == "update":
            body: dict[str, Any] = {}
            if name:
                body["name"] = name
            if position:
                body["pos"] = position
            if to_board_id:
                body["idBoard"] = api.ref(to_board_id)
            if not body:
                return {"error": "nothing to change: name, position or to_board_id"}
            return _bare(api.call(secrets, account, "PUT", f"/lists/{list_id}", json=body))
        if action in ("archive", "restore"):
            return _bare(api.call(secrets, account, "PUT", f"/lists/{list_id}/closed", json={"value": action == "archive"}))
        if action == "archive_cards":
            return _bare(api.call(secrets, account, "POST", f"/lists/{list_id}/archiveAllCards"))
        if action == "move_cards":
            if not to_list_id:
                return {"error": "move_cards needs to_list_id (and to_board_id when it is on another board)"}
            board = to_board_id or (api.data(api.call(secrets, account, "GET", f"/lists/{to_list_id}", params={"fields": "idBoard"})) or {}).get("idBoard")
            if not board:
                return {"error": f"no list {to_list_id!r}"}
            return _bare(api.call(secrets, account, "POST", f"/lists/{list_id}/moveAllCards", json={"idBoard": api.ref(board), "idList": to_list_id}))
        return {"error": "action is one of create, update, archive, restore, archive_cards, move_cards"}

    _tool(tools, trello_list, "trello_list",
          "Lists on a board. action=create (board_id, name, position), update (list_id: rename, position, "
          "to_board_id moves it), archive or restore (list_id), archive_cards (archives every card in it), "
          "move_cards (every card to to_list_id).",
          {"action": {"type": "string", "enum": ["create", "update", "archive", "restore", "archive_cards", "move_cards"]},
           "board_id": BOARD_ID, "list_id": {"type": "string"}, "name": {"type": "string"}, "position": {"type": "string"},
           "to_board_id": {"type": "string"}, "to_list_id": {"type": "string"}}, ["action"], "write")

    def trello_label(board_id: str = "", label_id: str = "", name: Optional[str] = None, color: Optional[str] = None, account: str = "") -> dict[str, Any]:
        if label_id:
            body: dict[str, Any] = {}
            if name is not None:
                body["name"] = name
            if color is not None:
                body["color"] = color or None
            if not body:
                return {"error": "nothing to change: name or color"}
            return _bare(api.call(secrets, account, "PUT", f"/labels/{label_id}", json=body))
        if not board_id:
            return {"error": "creating a label needs board_id"}
        made = api.call(secrets, account, "POST", "/labels", json={"idBoard": api.ref(board_id), "name": name or "", "color": color or None})
        label = api.data(made)
        return made if label is None else _bare(made, label={"id": label.get("id"), "name": label.get("name"), "color": label.get("color")})

    _tool(tools, trello_label, "trello_label",
          "Create a label on a board (board_id, name, color), or change one (label_id, name and/or color). "
          f"Colours: {_COLORS}. Put a label on a card with trello_update_card.",
          {"board_id": BOARD_ID, "label_id": {"type": "string"}, "name": {"type": "string"}, "color": {"type": "string"}}, [], "write")

    def trello_board(
        action: str, board_id: str = "", name: str = "", desc: Optional[str] = None, workspace_id: str = "",
        copy_from_board_id: str = "", default_lists: bool = True, archived: Optional[bool] = None, account: str = "",
    ) -> dict[str, Any]:
        if action == "create":
            if not name:
                return {"error": "create needs a name"}
            body: dict[str, Any] = {"name": name, "defaultLists": default_lists and not copy_from_board_id}
            if desc:
                body["desc"] = desc
            if workspace_id:
                body["idOrganization"] = workspace_id
            if copy_from_board_id:
                body["idBoardSource"] = api.ref(copy_from_board_id)
                body["keepFromSource"] = "cards"
            made = api.call(secrets, account, "POST", "/boards", json=body)
            board = api.data(made)
            return made if board is None else _bare(made, board={"id": board.get("id"), "name": board.get("name"), "url": board.get("url")})
        if action == "update":
            if not board_id:
                return {"error": "update needs board_id"}
            body = {}
            if name:
                body["name"] = name
            if desc is not None:
                body["desc"] = desc
            if archived is not None:
                body["closed"] = archived
            if not body:
                return {"error": "nothing to change: name, desc or archived"}
            return _bare(api.call(secrets, account, "PUT", f"/boards/{api.ref(board_id)}", json=body))
        return {"error": "action is create or update"}

    _tool(tools, trello_board, "trello_board",
          "Create a board (name, desc, workspace_id, default_lists, or copy_from_board_id to copy one with its cards), "
          "or update one (board_id: rename, desc, archived closes or reopens it).",
          {"action": {"type": "string", "enum": ["create", "update"]}, "board_id": BOARD_ID, "name": {"type": "string"},
           "desc": {"type": "string"}, "workspace_id": {"type": "string"}, "copy_from_board_id": BOARD_ID,
           "default_lists": {"type": "boolean"}, "archived": {"type": "boolean"}}, ["action"], "write")

    def trello_delete(what: str, id: str, card_id: str = "", checklist_id: str = "", account: str = "") -> dict[str, Any]:
        paths = {
            "card": lambda: f"/cards/{api.ref(id)}",
            "comment": lambda: f"/actions/{id}",
            "checklist": lambda: f"/checklists/{id}",
            "checklist_item": lambda: f"/checklists/{checklist_id}/checkItems/{id}" if checklist_id else "",
            "attachment": lambda: f"/cards/{api.ref(card_id)}/attachments/{id}" if card_id else "",
            "label": lambda: f"/labels/{id}",
        }
        if what not in paths:
            return {"error": f"what is one of {', '.join(paths)}"}
        path = paths[what]()
        if not path:
            return {"error": "checklist_item needs checklist_id; attachment needs card_id"}
        return _bare(api.call(secrets, account, "DELETE", path), deleted=what)

    _tool(tools, trello_delete, "trello_delete",
          "Delete for good, which Trello cannot undo: a card, comment, checklist, checklist item (with checklist_id), "
          "attachment (with card_id) or label. To set a card aside instead, archive it with trello_update_card.",
          {"what": {"type": "string", "enum": ["card", "comment", "checklist", "checklist_item", "attachment", "label"]},
           "id": {"type": "string"}, "card_id": {"type": "string"}, "checklist_id": {"type": "string"}}, ["what", "id"], "write")
    return tools
