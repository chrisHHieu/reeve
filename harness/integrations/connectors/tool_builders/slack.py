"""Slack tools: one callable per row of `slack_specs`, all on the Web API.

The callables are generic over the spec: turn what a person says
(`#general`, `@ana`) into ids, build the call, cut the answer down (`slack_shape`). The six
tools that are more than one call are the `HANDLERS` at the bottom.

Like every connector tool, a Slack tool is deferred (`integration_tools`): there are
about fifty and a session uses a handful, so they sit behind `tool_search` instead of on every
request.
"""
from __future__ import annotations

from ..toolkit import attach, function_schema
from ..html_text import html_to_text

import json
import re
import time
from typing import Any, Callable, Optional

from .. import slack_api, slack_shape
from ..slack_specs import READ, Arg, SlackTool
from ..slack_specs_write import ADMIN, WRITE
from harness.safety.secrets import SecretStore

SPECS: tuple[SlackTool, ...] = READ + WRITE + ADMIN

_CHANNEL_ID = re.compile(r"^[CGDW][A-Z0-9]{6,}$")
_USER_ID = re.compile(r"^[UW][A-Z0-9]{6,}$")

#: user id → display name, filled as messages are read. Names only: routing metadata,
#: never persisted (the same rule as the roster cache in `slack_directory`).
_NAMES: dict[str, str] = {}
#: More than this many unknown authors on one page is not worth one `users.info` each.
_NAME_LOOKUPS = 8

_TYPES = {"string": "string", "integer": "integer", "boolean": "boolean", "list": "array", "json": "object",
          "channel": "string", "user": "string"}


# -- what a person says → ids ----------------------------------------------------------


def _channel(secrets: SecretStore, value: str) -> tuple[str, Optional[dict[str, str]]]:
    """A channel id, or the id of `#name`. A person id passes through (chat.postMessage opens
    the DM); anything else is looked up in the same cached roster the channel picker uses."""
    raw = str(value or "").strip()
    if _CHANNEL_ID.match(raw) or _USER_ID.match(raw):
        return raw, None
    from ..slack_directory import list_channels

    name = raw.lstrip("#").lower()
    found = list_channels(secrets, name, limit=50)
    if not found.get("ok"):
        return "", {"error": str(found.get("error") or "could not list channels")}
    hits = [c for c in found.get("channels") or [] if str(c.get("name", "")).lower() == name]
    if len(hits) != 1:
        return "", {"error": f"no channel named #{name} that this bot can see; pass its id (C…), or invite the bot first"}
    return str(hits[0]["id"]), None


def _person(secrets: SecretStore, value: str) -> tuple[str, Optional[dict[str, str]]]:
    raw = str(value or "").strip()
    if _USER_ID.match(raw):
        return raw, None
    if "@" in raw and "." in raw.split("@")[-1] and not raw.startswith("@"):
        got = slack_api.call(secrets, "users.lookupByEmail", params={"email": raw}, read=True)
        if got.get("error"):
            return "", got
        return str(got["data"].get("user", {}).get("id") or ""), None
    from ..slack_directory import list_members

    handle = raw.lstrip("@").lower()
    found = list_members(secrets, handle, limit=50)
    if not found.get("ok"):
        return "", {"error": str(found.get("error") or "could not list people")}
    hits = [m for m in found.get("members") or [] if handle in (str(m.get("handle", "")).lower(), str(m.get("name", "")).lower())]
    if len(hits) != 1:
        return "", {"error": f"no single person matches {raw!r}; pass an id (U…) or an email"}
    return str(hits[0]["id"]), None


def names_for(secrets: SecretStore, ids: list[str]) -> dict[str, str]:
    """Display names for the authors on a page: the cache, then a few `users.info` calls."""
    out: dict[str, str] = {}
    lookups = 0
    for uid in ids:
        if uid in _NAMES:
            out[uid] = _NAMES[uid]
        elif uid.startswith(("U", "W")) and lookups < _NAME_LOOKUPS:
            lookups += 1
            got = slack_api.call(secrets, "users.info", params={"user": uid}, read=True)
            user = (got.get("data") or {}).get("user") or {}
            profile = user.get("profile") or {}
            name = profile.get("display_name") or profile.get("real_name") or user.get("name") or ""
            if name:
                _NAMES[uid] = name
                out[uid] = name
    return out


# -- the call --------------------------------------------------------------------------


def _wire(spec: SlackTool, args: dict[str, Any], secrets: SecretStore) -> tuple[dict[str, Any], Optional[dict[str, str]]]:
    out: dict[str, Any] = {}
    for arg in spec.args:
        value = args.get(arg.name)
        if value is None or value == "":
            if arg.required:
                return {}, {"error": f"`{arg.name}` is required"}
            continue
        if arg.kind == "channel":
            value, err = _channel(secrets, value)
        elif arg.kind == "user":
            value, err = _person(secrets, value)
        elif arg.kind == "list":
            items = value if isinstance(value, list) else [v for v in str(value).split(",") if v.strip()]
            resolved = []
            for item in items:
                one, err = _person(secrets, str(item)) if _looks_like_person(str(item)) else (str(item), None)
                if err:
                    return {}, err
                resolved.append(one)
            value, err = (resolved if spec.json_body else ",".join(resolved)), None
        elif arg.kind == "json":
            value, err = _json(arg, value)
        else:
            err = None
        if err:
            return {}, err
        out[arg.sent_as] = value
    return out, None


def _looks_like_person(text: str) -> bool:
    return text.startswith("@") or "@" in text


def _json(arg: Arg, value: Any) -> tuple[Any, Optional[dict[str, str]]]:
    if isinstance(value, (dict, list)):
        return value, None
    try:
        return json.loads(str(value)), None
    except ValueError:
        return None, {"error": f"`{arg.name}` must be valid JSON"}


def _pre(spec: SlackTool, wire: dict[str, Any]) -> dict[str, Any]:
    """The few tools whose model-facing arguments are not the method's own shape."""
    if spec.name == "slack_create_canvas":
        markdown = wire.pop("markdown", "")
        wire["document_content"] = {"type": "markdown", "markdown": markdown}
    elif spec.name == "slack_set_status":
        wire = {"profile": {k: wire[k] for k in ("status_text", "status_emoji", "status_expiration") if k in wire}}
    return wire


def run(spec: SlackTool, secrets: SecretStore, args: dict[str, Any]) -> dict[str, Any]:
    args = dict(args)
    handler = HANDLERS.get(spec.handler)
    if spec.handler and handler is not None:
        return handler(spec, secrets, args)
    wire, err = _wire(spec, args, secrets)
    if err:
        return err
    wire = _pre(spec, wire)
    got = slack_api.call(
        secrets, spec.method, kind=spec.token,
        params=wire if spec.kind == "read" else None, body=None if spec.kind == "read" else wire,
        as_json=spec.json_body, read=spec.kind == "read",
    )
    if got.get("error"):
        return got
    data = got["data"]
    if spec.shape == "messages":
        return slack_shape.messages(data, names_for(secrets, slack_shape.user_ids(data)))
    return slack_shape.SHAPES.get(spec.shape, slack_shape.plain)(data)


# -- tools that are more than one call ----------------------------------------------------


def _list_channels(spec, secrets, args):
    from ..slack_directory import list_channels

    found = list_channels(secrets, str(args.get("query") or ""), int(args.get("limit") or 25))
    return found if found.get("ok") else {"error": str(found.get("error"))}


def _list_users(spec, secrets, args):
    from ..slack_directory import list_members

    found = list_members(secrets, str(args.get("query") or ""), int(args.get("limit") or 25))
    return found if found.get("ok") else {"error": str(found.get("error"))}


def _get_user(spec, secrets, args):
    who = str(args.get("user") or "").strip()
    if not who:
        return {"error": "`user` is required"}
    uid, err = _person(secrets, who)
    if err:
        return err
    got = slack_api.call(secrets, "users.info", params={"user": uid}, read=True)
    if got.get("error"):
        return got
    u = got["data"].get("user") or {}
    p = u.get("profile") or {}
    row = {"id": u.get("id", uid), "name": p.get("display_name") or p.get("real_name") or u.get("name", ""),
           "handle": u.get("name", ""), "title": p.get("title", ""), "tz": u.get("tz", ""),
           "is_bot": bool(u.get("is_bot")), "deleted": bool(u.get("deleted")),
           "status": " ".join(x for x in (p.get("status_emoji"), p.get("status_text")) if x)}
    if p.get("email"):
        row["email"] = p["email"]
    return row


def _get_message(spec, secrets, args):
    channel, err = _channel(secrets, str(args.get("channel") or ""))
    ts = str(args.get("ts") or "")
    if err or not ts:
        return err or {"error": "`ts` is required"}
    got = slack_api.call(secrets, "conversations.history", read=True,
                         params={"channel": channel, "latest": ts, "inclusive": "true", "limit": 1})
    if got.get("error"):
        return got
    rows = [m for m in got["data"].get("messages") or [] if m.get("ts") == ts]
    if not rows:
        return {"error": slack_api.explain("message_not_found", {})}
    link = slack_api.call(secrets, "chat.getPermalink", read=True, params={"channel": channel, "message_ts": ts})
    out = slack_shape.message(rows[0], names_for(secrets, [rows[0].get("user", "")]))
    out["channel"] = channel
    if link.get("ok"):
        out["permalink"] = link["data"].get("permalink", "")
    return out


def _read_canvas(spec, secrets, args):
    canvas = str(args.get("canvas_id") or "")
    info = slack_api.call(secrets, "files.info", params={"file": canvas}, read=True)
    if info.get("error"):
        return info
    f = info["data"].get("file") or {}
    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        return {"error": "that file has no readable content"}
    token, terr = slack_api.token_for(secrets, "bot")
    if terr:
        return terr
    try:
        resp = slack_api._send("GET", url, headers={"Authorization": f"Bearer {token}"}, params=None, data=None, json=None)
    except Exception as exc:
        return {"error": f"could not download the canvas: {exc.__class__.__name__}"}
    if resp.status_code >= 400:
        return {"error": f"the canvas download answered HTTP {resp.status_code}"}
    return {"title": f.get("title") or f.get("name") or "", "text": slack_shape.clip(html_to_text(resp.text), 60_000)}


def _user_status(spec, secrets, args):
    uid, err = _person(secrets, str(args.get("user") or ""))
    if err:
        return err
    out: dict[str, Any] = {"user": uid}
    presence = slack_api.call(secrets, "users.getPresence", params={"user": uid}, read=True)
    out["presence"] = presence["data"].get("presence") if presence.get("ok") else f"unknown ({presence.get('error')})"
    dnd = slack_api.call(secrets, "dnd.info", params={"user": uid}, read=True)
    if dnd.get("ok"):
        d = dnd["data"]
        now = time.time()
        in_window = bool(d.get("dnd_enabled")) and (d.get("next_dnd_start_ts") or 0) <= now <= (d.get("next_dnd_end_ts") or 0)
        out["do_not_disturb_now"] = bool(d.get("snooze_enabled")) or in_window
    return out


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_channels": _list_channels,
    "list_users": _list_users,
    "get_user": _get_user,
    "get_message": _get_message,
    "read_canvas": _read_canvas,
    "user_status": _user_status,
}


# -- the callables ---------------------------------------------------------------------


def schema_for(spec: SlackTool) -> dict[str, Any]:
    props: dict[str, Any] = {
        a.name: {"type": _TYPES[a.kind], "description": a.description, **({"items": {"type": "string"}} if a.kind == "list" else {})}
        for a in spec.args
    }
    return function_schema(spec.name, spec.description, props, [a.name for a in spec.args if a.required])


def slack_tools(secrets: SecretStore, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    for spec in SPECS:
        def fn(_spec: SlackTool = spec, **kwargs: Any) -> dict[str, Any]:
            return run(_spec, secrets, kwargs)

        fn.__name__ = spec.name
        attach(fn, schema_for(spec), caps=["slack", spec.kind])
        tools.append(fn)
    return tools
