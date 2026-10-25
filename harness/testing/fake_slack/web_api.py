"""The Web API half of the fake: which methods it answers, and what it remembers of the calls.

`WebApi` owns the directory (users, channels) and the record of what was asked of it. It knows
nothing about HTTP or sockets: `answer(method, params)` is a plain function of its state, and
`read_params` is the only part that touches a request.
"""

from __future__ import annotations

import logging
from typing import Callable

from starlette.requests import Request

from .envelopes import TEAM_ID, maybe_json

logger = logging.getLogger("harness.testing.fake_slack")


class Clock:
    """Message timestamps: strictly increasing, shaped like Slack's `seconds.sequence`."""

    def __init__(self) -> None:
        self._base = 1_700_000_000
        self._seq = 0

    def next(self) -> str:
        self._seq += 1
        return f"{self._base + self._seq}.{self._seq:06d}"


async def read_params(request: Request) -> dict:
    """All the parameters of a call. The SDK sends reads as a query string and the rest as a form,
    and the stateless senders post JSON, so the three sources are merged."""
    params: dict = {key: maybe_json(value) for key, value in request.query_params.items()}
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except ValueError:
            return params
        if isinstance(body, dict):
            params.update(body)
        return params
    try:
        form = await request.form()
    except Exception:
        return params
    params.update({key: maybe_json(value) for key, value in form.items()})
    return params


class WebApi:
    def __init__(self, clock: Clock, bot_user_id: str, socket_url: Callable[[], str]) -> None:
        self.clock = clock
        self.bot_user_id = bot_user_id
        self._socket_url = socket_url
        self.users: dict[str, dict] = {}
        self.channels: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.unknown_methods: list[str] = []
        self.calls: list[str] = []  # every method, in order, for assertions about caching
        self._methods: dict[str, Callable[[dict], dict]] = {
            "auth.test": self._auth_test,
            "apps.connections.open": self._open_connection,
            "users.info": self._user_info,
            "conversations.info": self._channel_info,
            "chat.postMessage": self._post_message,
            "chat.update": self._update_message,
        }

    def add_user(self, id: str, name: str, real_name: str | None, display_name: str | None) -> None:
        real = real_name or name
        self.users[id] = {
            "id": id,
            "name": name,
            "real_name": real,
            "profile": {"display_name": display_name or name, "real_name": real},
        }

    def add_channel(self, id: str, name: str, is_im: bool) -> None:
        self.channels[id] = {"id": id, "name": name, "is_im": bool(is_im)}

    def clear(self) -> None:
        for held in (self.users, self.channels, self.sent, self.unknown_methods, self.calls):
            held.clear()

    def answer(self, method: str, params: dict) -> dict:
        self.calls.append(method)
        handler = self._methods.get(method)
        if handler is None:
            # A method nobody taught the fake: answer ok, but leave a trace of the gap.
            self.unknown_methods.append(method)
            logger.info("FakeSlack: unhandled Web API method %s (params=%s)", method, params)
            return {"ok": True}
        return handler(params)

    # -- the methods ---------------------------------------------------------------------

    def _auth_test(self, params: dict) -> dict:
        return {
            "ok": True,
            "url": "https://fake.slack.local/",
            "team": "FakeTeam",
            "user": "fakebot",
            "team_id": TEAM_ID,
            "user_id": self.bot_user_id,
            "bot_id": "B_FAKE",
            "is_enterprise_install": False,
        }

    def _open_connection(self, params: dict) -> dict:
        return {"ok": True, "url": self._socket_url()}

    def _user_info(self, params: dict) -> dict:
        return self._lookup(self.users, params.get("user"), "user", "user_not_found")

    def _channel_info(self, params: dict) -> dict:
        return self._lookup(self.channels, params.get("channel"), "channel", "channel_not_found")

    @staticmethod
    def _lookup(table: dict[str, dict], key: object, field: str, missing: str) -> dict:
        found = table.get(str(key or ""))
        return {"ok": True, field: found} if found is not None else {"ok": False, "error": missing}

    def _post_message(self, params: dict) -> dict:
        ts = self.clock.next()
        self.sent.append(
            {
                "method": "chat.postMessage",
                "channel": params.get("channel"),
                "text": params.get("text"),
                "blocks": maybe_json(params.get("blocks")),
                "thread_ts": params.get("thread_ts"),
                "ts": ts,
            }
        )
        return {"ok": True, "ts": ts, "channel": params.get("channel")}

    def _update_message(self, params: dict) -> dict:
        ts = params.get("ts") or self.clock.next()
        self.sent.append(
            {
                "method": "chat.update",
                "channel": params.get("channel"),
                "text": params.get("text"),
                "blocks": maybe_json(params.get("blocks")),
                "ts": ts,
            }
        )
        return {"ok": True, "ts": ts, "channel": params.get("channel")}
