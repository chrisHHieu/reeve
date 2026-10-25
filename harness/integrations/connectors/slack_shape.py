"""What a Slack response is cut down to before a model sees it.

Slack answers are large and mostly bookkeeping: every message carries block trees, team ids,
client message ids and bot profiles. A model needs who said what, when, in which thread, with
what attached. Each shape keeps that and drops the rest, and clips long text with a marker
so a cut is never mistaken for the whole (the harness rule: bound results, say so).
"""

from __future__ import annotations

from typing import Any, Callable

#: One message's text is clipped here; a longer one says how much was cut.
TEXT_CLIP = 3000


def clip(text: Any, limit: int = TEXT_CLIP) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [{len(text) - limit} more characters]"


def message(m: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """One message: who, when, what, and the parts that let a reader follow it."""
    out: dict[str, Any] = {"ts": m.get("ts", ""), "text": clip(m.get("text"))}
    user = m.get("user") or m.get("bot_id") or ""
    if user:
        out["user"] = user
        if names and user in names:
            out["user_name"] = names[user]
    if m.get("subtype"):
        out["subtype"] = m["subtype"]
    if m.get("thread_ts") and m.get("thread_ts") != m.get("ts"):
        out["thread_ts"] = m["thread_ts"]
    if m.get("reply_count"):
        out["reply_count"] = m["reply_count"]
    files = [
        {"id": f.get("id", ""), "name": f.get("name") or f.get("title") or "", "type": f.get("filetype", "")}
        for f in (m.get("files") or [])
        if isinstance(f, dict)
    ]
    if files:
        out["files"] = files[:10]
    reactions = [{"name": r.get("name", ""), "count": r.get("count", 0)} for r in (m.get("reactions") or [])]
    if reactions:
        out["reactions"] = reactions[:15]
    return out


def user_ids(data: dict[str, Any]) -> list[str]:
    """The distinct people a page of messages mentions as authors, in order."""
    seen: list[str] = []
    for m in data.get("messages") or []:
        uid = m.get("user")
        if uid and uid not in seen:
            seen.append(uid)
    return seen


def _paged(data: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = dict(fields)
    cursor = (data.get("response_metadata") or {}).get("next_cursor")
    if cursor:
        out["next_cursor"] = cursor
    return out


def messages(data: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    rows = [message(m, names) for m in data.get("messages") or [] if isinstance(m, dict)]
    return _paged(data, messages=rows, has_more=bool(data.get("has_more")))


def channel(data: dict[str, Any]) -> dict[str, Any]:
    c = data.get("channel") or {}
    out = {
        "id": c.get("id", ""),
        "name": c.get("name") or c.get("name_normalized") or "",
        "is_private": bool(c.get("is_private")),
        "is_archived": bool(c.get("is_archived")),
        "is_member": bool(c.get("is_member")),
    }
    for key in ("num_members", "created"):
        if c.get(key) is not None:
            out[key] = c[key]
    for key in ("topic", "purpose"):
        value = (c.get(key) or {}).get("value")
        if value:
            out[key] = value
    return out


def channels(data: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {"id": c.get("id", ""), "name": c.get("name", ""), "is_private": bool(c.get("is_private")), "is_im": bool(c.get("is_im"))}
        for c in data.get("channels") or []
    ]
    return _paged(data, channels=rows)


def members(data: dict[str, Any]) -> dict[str, Any]:
    ids = data.get("members") or data.get("users") or []
    return _paged(data, members=list(ids))


def usergroups(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("usergroup"):
        data = {"usergroups": [data["usergroup"]]}
    rows = []
    for g in data.get("usergroups") or []:
        row = {"id": g.get("id", ""), "handle": g.get("handle", ""), "name": g.get("name", ""), "user_count": g.get("user_count", 0)}
        if g.get("users"):
            row["users"] = g["users"]
        rows.append(row)
    return {"usergroups": rows}


def file_row(f: dict[str, Any]) -> dict[str, Any]:
    out = {"id": f.get("id", ""), "name": f.get("name") or f.get("title") or "", "type": f.get("filetype", ""), "size": f.get("size", 0)}
    for key in ("user", "created", "permalink"):
        if f.get(key):
            out[key] = f[key]
    return out


def file(data: dict[str, Any]) -> dict[str, Any]:
    return file_row(data.get("file") or {})


def files(data: dict[str, Any]) -> dict[str, Any]:
    return {"files": [file_row(f) for f in data.get("files") or []], "total": (data.get("paging") or {}).get("total")}


def search(data: dict[str, Any]) -> dict[str, Any]:
    block = data.get("messages") or {}
    rows = []
    for m in block.get("matches") or []:
        row = message(m)
        row["channel"] = (m.get("channel") or {}).get("id", "")
        row["channel_name"] = (m.get("channel") or {}).get("name", "")
        if m.get("permalink"):
            row["permalink"] = m["permalink"]
        rows.append(row)
    paging = block.get("paging") or {}
    return {"matches": rows, "total": block.get("total", paging.get("total")), "page": paging.get("page")}


def search_files(data: dict[str, Any]) -> dict[str, Any]:
    block = data.get("files") or {}
    return {"matches": [file_row(f) for f in block.get("matches") or []], "total": block.get("total", (block.get("paging") or {}).get("total"))}


def reactions(data: dict[str, Any]) -> dict[str, Any]:
    m = data.get("message") or {}
    return {"reactions": [{"name": r.get("name", ""), "count": r.get("count", 0), "users": r.get("users", [])} for r in m.get("reactions") or []]}


def pins(data: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in data.get("items") or []:
        m = item.get("message") or {}
        rows.append({"type": item.get("type", ""), "ts": m.get("ts", ""), "text": clip(m.get("text"), 500), "user": m.get("user", "")})
    return {"pins": rows}


def posted(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "channel": data.get("channel", ""), "ts": data.get("ts") or (data.get("message") or {}).get("ts", "")}


def scheduled(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "scheduled_message_id": data.get("scheduled_message_id", ""), "post_at": data.get("post_at"), "channel": data.get("channel", "")}


def plain(data: dict[str, Any]) -> dict[str, Any]:
    """Everything but Slack's own envelope."""
    return {k: v for k, v in data.items() if k not in ("ok", "response_metadata", "warning")}


SHAPES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "plain": plain,
    "channel": channel,
    "channels": channels,
    "members": members,
    "usergroups": usergroups,
    "file": file,
    "files": files,
    "search": search,
    "search_files": search_files,
    "reactions": reactions,
    "pins": pins,
    "posted": posted,
    "scheduled": scheduled,
}
