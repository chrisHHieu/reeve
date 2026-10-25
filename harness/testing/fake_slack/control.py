"""The HTTP control surface of the fake: the same scenario verbs as the object, for curl and for the
standalone runner. Each handler reads a JSON body, calls the matching `FakeSlack` method and answers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

if TYPE_CHECKING:
    from .server import FakeSlack

_OK = {"ok": True}


def control_routes(fake: "FakeSlack") -> list[BaseRoute]:
    def post(path: str, act: Callable[[dict], Awaitable[dict]]) -> Route:
        async def endpoint(request: Request) -> JSONResponse:
            return JSONResponse(await act(await request.json()))

        return Route(f"/control/{path}", endpoint, methods=["POST"])

    def get(path: str, read: Callable[[], dict]) -> Route:
        async def endpoint(request: Request) -> JSONResponse:
            return JSONResponse(read())

        return Route(f"/control/{path}", endpoint, methods=["GET"])

    async def users(body: dict) -> dict:
        fake.add_user(body["id"], body["name"], body.get("real_name"), body.get("display_name"))
        return _OK

    async def channels(body: dict) -> dict:
        fake.add_channel(body["id"], body["name"], bool(body.get("is_im")))
        return _OK

    async def inbound(body: dict) -> dict:
        ts = await fake.inbound(
            channel=body["channel"],
            user=body["user"],
            text=body["text"],
            thread_ts=body.get("thread_ts"),
            channel_type=body.get("channel_type"),
        )
        return {"ok": True, "ts": ts}

    async def interaction(body: dict) -> dict:
        await fake.interaction(
            channel=body["channel"],
            user=body["user"],
            username=body.get("username") or body["user"],
            message_ts=body["message_ts"],
            action_id=body["action_id"],
            value=body.get("value", ""),
        )
        return _OK

    async def reset(body: dict) -> dict:
        await fake.reset()
        return _OK

    return [
        post("users", users),
        post("channels", channels),
        post("inbound", inbound),
        post("interaction", interaction),
        get("outbound", lambda: {"outbound": fake.outbound()}),
        _reset_route(reset),
        get("health", lambda: {"ok": True, "sockets": fake.live_sockets}),
    ]


def _reset_route(act: Callable[[dict], Awaitable[dict]]) -> Route:
    # Unlike the other writes, reset takes no body: it must not insist on JSON.
    async def endpoint(request: Request) -> JSONResponse:
        return JSONResponse(await act({}))

    return Route("/control/reset", endpoint, methods=["POST"])
