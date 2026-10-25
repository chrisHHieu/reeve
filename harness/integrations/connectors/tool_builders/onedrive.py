"""OneDrive's tools through Microsoft Graph: find, list, save, upload, organize and share.

A file is named by its path in the drive ("/Reports/q3.xlsx") or by its id; a file shared with the
person lives in someone else's drive, so its rows carry that `drive_id` and a call passes it back.
Reading is saving into the session folder: the agent's own file reader already reads Word, Excel,
PowerPoint, PDF and text, so a download is how a document is read, and it runs unasked like any
read (it lands only in the session's folder). Uploading, moving, sharing and deleting ask.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from .. import calls, session_files
from ..headers import graph_headers
from ..profiles import GENERIC_ACCOUNT_PROP, account_profile, account_result
from ..toolkit import attach, clamp, function_schema

GRAPH = "https://graph.microsoft.com/v1.0"
#: Graph's simple upload stops at 4 MB; past it an upload session sends the file in chunks.
SIMPLE_UPLOAD = 4 * 1024 * 1024
#: Each chunk a multiple of 320 KiB, as Graph requires.
CHUNK = 320 * 1024 * 32
MAX_UPLOAD = 250 * 1024 * 1024
MAX_DOWNLOAD = 250 * 1024 * 1024
_ITEM = {"type": "string", "description": "A path in the drive (/Reports/q3.xlsx) or an item id."}
_DRIVE = {"type": "string", "description": "Only for a file shared with you: the drive_id its row gave."}
_FIELDS = "id,name,size,lastModifiedDateTime,webUrl,folder,file,parentReference,remoteItem"


def item_path(item: str, drive_id: str = "") -> str:
    """The Graph path of an item named by its drive path or its id."""
    drive = f"/drives/{quote(drive_id)}" if drive_id else "/me/drive"
    text = (item or "").strip()
    if not text or text == "/":
        return f"{drive}/root"
    if text.startswith("/"):
        return f"{drive}/root:{quote(text.rstrip('/'))}:"
    return f"{drive}/items/{quote(text)}"


def item_row(item: dict[str, Any]) -> dict[str, Any]:
    remote = item.get("remoteItem") or {}
    source = remote or item
    parent = source.get("parentReference") or {}
    row: dict[str, Any] = {"id": source.get("id") or item.get("id"), "name": item.get("name") or source.get("name"),
                           "kind": "folder" if source.get("folder") else "file", "url": source.get("webUrl") or item.get("webUrl")}
    if source.get("size") is not None and not source.get("folder"):
        row["bytes"] = source.get("size")
    if source.get("lastModifiedDateTime") or item.get("lastModifiedDateTime"):
        row["modified"] = source.get("lastModifiedDateTime") or item.get("lastModifiedDateTime")
    if parent.get("path"):
        row["folder"] = parent["path"].split("root:", 1)[-1] or "/"
    if remote and parent.get("driveId"):
        row["drive_id"] = parent["driveId"]
        shared = (remote.get("shared") or {}).get("sharedBy") or (remote.get("createdBy") or {})
        name = (shared.get("user") or {}).get("displayName")
        if name:
            row["shared_by"] = name
    if (source.get("folder") or {}).get("childCount") is not None:
        row["items"] = source["folder"]["childCount"]
    return row


def onedrive_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def profile_for(account: str):
        return account_profile(secrets, "onedrive", account, "access_token")

    def graph(account: str, method: str, path: str, **options: Any) -> dict[str, Any]:
        aid, profile, failure = profile_for(account)
        if failure:
            return failure
        return account_result(aid, calls.request(method, GRAPH + path, headers=graph_headers(profile["access_token"]), **options))

    def bare(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**{k: v for k, v in result.items() if k != "data"}, **extra} if result.get("ok") else result

    def add(fn: Callable[..., Any], description: str, props: dict[str, Any], required: list[str], kind: str) -> None:
        schema = function_schema(fn.__name__, description, {**props, "account": GENERIC_ACCOUNT_PROP}, required)
        tools.append(attach(fn, schema, caps=["onedrive", kind]))

    def listing(result: dict[str, Any], limit: int) -> dict[str, Any]:
        if not result.get("ok"):
            return result
        values = (result.get("data") or {}).get("value") or []
        return bare(result, items=[item_row(i) for i in values[:limit]], **({"more": True} if len(values) > limit or (result.get("data") or {}).get("@odata.nextLink") else {}))

    def onedrive_search(query: str, max_results: int = 25, account: str = "") -> dict[str, Any]:
        limit = clamp(max_results, default=25, ceiling=100)
        safe = query.replace("'", "''")
        return listing(graph(account, "GET", f"/me/drive/root/search(q='{quote(safe)}')", params={"$top": limit, "$select": _FIELDS}), limit)

    add(onedrive_search, "Search file and folder names and contents in this OneDrive, and files shared with you.",
        {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"], "read")

    def onedrive_list(folder: str = "/", shared_with_me: bool = False, recent: bool = False, drive_id: str = "",
                      max_results: int = 100, account: str = "") -> dict[str, Any]:
        limit = clamp(max_results, default=100, ceiling=500)
        if shared_with_me:
            return listing(graph(account, "GET", "/me/drive/sharedWithMe"), limit)
        if recent:
            return listing(graph(account, "GET", "/me/drive/recent"), limit)
        return listing(graph(account, "GET", f"{item_path(folder, drive_id)}/children", params={"$top": limit, "$select": _FIELDS, "$orderby": "name"}), limit)

    add(onedrive_list, "List a folder (a path, / for the top), or the files shared with you (shared_with_me), or the ones opened lately (recent).",
        {"folder": _ITEM, "shared_with_me": {"type": "boolean"}, "recent": {"type": "boolean"}, "drive_id": _DRIVE,
         "max_results": {"type": "integer"}}, [], "read")

    def onedrive_download(item: str, drive_id: str = "", account: str = "") -> dict[str, Any]:
        meta = graph(account, "GET", item_path(item, drive_id), params={"$select": "id,name,size,file,folder,@microsoft.graph.downloadUrl"})
        info = meta.get("data") if meta.get("ok") else None
        if info is None:
            return meta
        if info.get("folder"):
            return {"error": f"{info.get('name')} is a folder; list it instead"}
        if int(info.get("size") or 0) > MAX_DOWNLOAD:
            return {"error": f"the file is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB"}
        url = info.get("@microsoft.graph.downloadUrl")
        if not url:
            return {"error": "OneDrive gave no download link for this file"}
        target, refusal = session_files.landing(info.get("name") or "file", roots, remote=remote)
        if refusal:
            return {"error": refusal}
        payload, failure = api_fetch(url)
        if failure:
            return {"error": failure}
        target.write_bytes(payload)
        return bare(meta, path=str(target), size=len(payload),
                    read_with="the file reader (Word, Excel, PowerPoint, PDF and text are read as text)")

    add(onedrive_download, "Save a file into the session folder so it can be read (Word, Excel, PowerPoint, PDF, text) or worked on.",
        {"item": _ITEM, "drive_id": _DRIVE}, ["item"], "read")

    def onedrive_upload(path: str, folder: str = "/", name: str = "", if_exists: str = "rename", account: str = "") -> dict[str, Any]:
        if if_exists not in ("rename", "replace", "fail"):
            return {"error": "if_exists is rename, replace or fail"}
        file, refusal = session_files.outgoing(path, roots, remote=remote, limit=MAX_UPLOAD, service="OneDrive")
        if refusal:
            return {"error": refusal}
        aid, profile, failure = profile_for(account)
        if failure:
            return failure
        target = "/" + "/".join(p for p in (folder.strip("/"), name or file.name) if p)
        result = api_upload(profile["access_token"], f"{GRAPH}{item_path(target)}", file, if_exists)
        uploaded = result.get("data") if result.get("ok") else None
        if uploaded is None:
            return result
        return account_result(aid, {"ok": True, "item": item_row(uploaded)})

    add(onedrive_upload, "Upload a file from the session folder into a OneDrive folder (if_exists: rename, replace or fail).",
        {"path": {"type": "string"}, "folder": _ITEM, "name": {"type": "string"},
         "if_exists": {"type": "string", "enum": ["rename", "replace", "fail"]}}, ["path"], "write")

    def onedrive_create_folder(name: str, parent: str = "/", account: str = "") -> dict[str, Any]:
        made = graph(account, "POST", f"{item_path(parent)}/children",
                     json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename"})
        return bare(made, item=item_row(made.get("data") or {})) if made.get("ok") else made

    add(onedrive_create_folder, "Create a folder (under parent, a path; / for the top).",
        {"name": {"type": "string"}, "parent": _ITEM}, ["name"], "write")

    def onedrive_move(item: str, new_name: str = "", to_folder: str = "", account: str = "") -> dict[str, Any]:
        change: dict[str, Any] = {}
        if new_name:
            change["name"] = new_name
        if to_folder:
            parent = graph(account, "GET", item_path(to_folder), params={"$select": "id"})
            if not parent.get("ok"):
                return parent
            change["parentReference"] = {"id": (parent.get("data") or {}).get("id")}
        if not change:
            return {"error": "nothing to change: new_name or to_folder"}
        moved = graph(account, "PATCH", item_path(item), json=change)
        return bare(moved, item=item_row(moved.get("data") or {})) if moved.get("ok") else moved

    add(onedrive_move, "Rename a file or folder, or move it to another folder.",
        {"item": _ITEM, "new_name": {"type": "string"}, "to_folder": _ITEM}, ["item"], "write")

    def onedrive_share(item: str, access: str = "view", who: str = "organization", emails: Optional[list[str]] = None,
                       message: str = "", account: str = "") -> dict[str, Any]:
        if access not in ("view", "edit"):
            return {"error": "access is view or edit"}
        if who == "people":
            if not emails:
                return {"error": "sharing with people needs their emails"}
            invited = graph(account, "POST", f"{item_path(item)}/invite", json={
                "recipients": [{"email": e} for e in emails], "message": message, "requireSignIn": True,
                "sendInvitation": True, "roles": ["write" if access == "edit" else "read"]})
            return bare(invited, shared_with=emails, access=access)
        if who not in ("organization", "anyone"):
            return {"error": "who is organization, anyone or people"}
        link = graph(account, "POST", f"{item_path(item)}/createLink",
                     json={"type": access, "scope": "organization" if who == "organization" else "anonymous"})
        made = link.get("data") if link.get("ok") else None
        return link if made is None else bare(link, url=(made.get("link") or {}).get("webUrl"), access=access, who=who)

    add(onedrive_share,
        "Share a file or folder: a link for the organization or for anyone with it (a personal account has only anyone), "
        "or an invitation to people by email. access is view or edit.",
        {"item": _ITEM, "access": {"type": "string", "enum": ["view", "edit"]},
         "who": {"type": "string", "enum": ["organization", "anyone", "people"]},
         "emails": {"type": "array", "items": {"type": "string"}}, "message": {"type": "string"}}, ["item"], "write")

    def onedrive_delete(item: str, account: str = "") -> dict[str, Any]:
        return bare(graph(account, "DELETE", item_path(item)), deleted=item, restore="from the OneDrive recycle bin")

    add(onedrive_delete, "Delete a file or folder. It goes to the OneDrive recycle bin, where it can be restored for a while.",
        {"item": _ITEM}, ["item"], "write")
    return tools


def api_fetch(url: str) -> tuple[bytes, str]:
    """(bytes, "") or (b"", why). The download link is pre-authorized and short-lived, so it takes
    no token; a module function so a test can stand in for the network."""
    try:
        import httpx

        with httpx.Client(timeout=300.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return b"", f"OneDrive did not hand over the file (HTTP {response.status_code})"
        return response.content, ""
    except Exception as exc:
        return b"", f"the download failed: {exc.__class__.__name__}"


def api_upload(token: str, target: str, file: Path, if_exists: str) -> dict[str, Any]:
    """One file into OneDrive: a single PUT up to 4 MB, else an upload session sent in chunks.
    `target` is the Graph path of the item to create (`…/root:/folder/name:`)."""
    try:
        import httpx

        headers = {"Authorization": f"Bearer {token}"}
        size = file.stat().st_size
        with httpx.Client(timeout=300.0) as client:
            if size <= SIMPLE_UPLOAD:
                response = client.put(f"{target}/content", params={"@microsoft.graph.conflictBehavior": if_exists},
                                      headers=headers, content=file.read_bytes())
            else:
                opened = client.post(f"{target}/createUploadSession", headers=headers,
                                     json={"item": {"@microsoft.graph.conflictBehavior": if_exists}})
                if opened.status_code >= 400:
                    return {"error": f"HTTP {opened.status_code}", "details": opened.text[:300]}
                url = opened.json()["uploadUrl"]
                with file.open("rb") as handle:
                    start = 0
                    while start < size:
                        chunk = handle.read(CHUNK)
                        end = start + len(chunk) - 1
                        # The session URL is itself the credential; Graph refuses a token on it.
                        response = client.put(url, content=chunk, headers={"Content-Range": f"bytes {start}-{end}/{size}"})
                        if response.status_code >= 400:
                            return {"error": f"HTTP {response.status_code}", "details": response.text[:300]}
                        start = end + 1
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "details": response.text[:300]}
        return {"ok": True, "data": response.json()}
    except Exception as exc:
        return {"error": f"the upload failed: {exc.__class__.__name__}"}
