"""The real Slack adapter and slack_bolt stack, driven against the in-process FakeSlack.

These follow the acceptance list in `platform/docs/FAKE-SLACK-SPEC.md`. They need no Slack and run
in CI (not marked `integration`): the `fake_slack` fixture (conftest.py) starts the fake and points
`SLACK_API_URL` at it.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager

import httpx

from harness.integrations.connectors.config import ConnectorSettings
from harness.integrations.connectors.messaging import MessageEvent, SlackAdapter
from harness.integrations.connectors.messaging.gateway import Gateway


def _open_to_all() -> dict[str, ConnectorSettings]:
    return {"slack": ConnectorSettings(platform="slack", enabled=True, allow_all=True)}


async def _send(adapter, *args, **kwargs):
    """A send is blocking httpx, run on a worker thread in the app; run here on the fake's own loop,
    it would starve the fake. So it gets a thread and a loop of its own, as in the app."""
    return await asyncio.to_thread(asyncio.run, adapter.send(*args, **kwargs))


class _Heard:
    """What the gateway handed on, and an event to wait for the next one."""

    def __init__(self):
        self.events: list[MessageEvent] = []
        self.arrived = asyncio.Event()

    async def __call__(self, event: MessageEvent):
        self.events.append(event)
        self.arrived.set()

    async def next(self, fake_slack, **message) -> MessageEvent:
        self.arrived.clear()
        await fake_slack.inbound(**message)
        await asyncio.wait_for(self.arrived.wait(), timeout=5)
        return self.events[-1]


@asynccontextmanager
async def _connected(fake_slack, **adapter_options):
    heard = _Heard()
    gateway = Gateway(settings=_open_to_all(), handler=heard)
    adapter = SlackAdapter("xoxb-test", "xapp-test", **adapter_options)
    gateway.register(adapter)
    await adapter.connect()
    try:
        await fake_slack.wait_socket()
        yield adapter, heard
    finally:
        await adapter.disconnect()


async def test_connecting_learns_the_bots_id_and_hears_the_sockets_hello(fake_slack):
    adapter = SlackAdapter("xoxb-test", "xapp-test")
    connected = await adapter.connect()
    # Listening starts before anything is awaited, so the greeting cannot be missed.
    greeted = asyncio.Event()

    async def listen(client, message, raw):
        if message.get("type") == "hello":
            greeted.set()

    adapter._socket.client.message_listeners.append(listen)
    try:
        assert connected is True and adapter._bot_user_id == "U_BOT"  # from auth.test
        await fake_slack.wait_socket()
        await asyncio.wait_for(greeted.wait(), timeout=5)
    finally:
        await adapter.disconnect()


async def test_a_channel_message_names_its_sender_from_one_lookup(fake_slack):
    fake_slack.add_user("U1", "alice", real_name="Alice Real", display_name="Alice Display")
    fake_slack.add_channel("C1", "general", is_im=False)
    async with _connected(fake_slack) as (adapter, heard):
        event = await heard.next(fake_slack, channel="C1", user="U1", text="hello team")
        assert (event.text, event.source.chat_id, event.source.chat_type, event.source.user_name) == ("hello team", "C1", "channel", "Alice Display")
        assert (await heard.next(fake_slack, channel="C1", user="U1", text="again")).source.user_name == "Alice Display"
        assert fake_slack.api_calls.count("users.info") == 1  # the second name came from the cache
        # The fake also answers conversations.info, which naming a channel is built on.
        info = await adapter._app.client.conversations_info(channel="C1")
        assert info["channel"]["name"] == "general" and info["channel"]["is_im"] is False


async def test_a_mention_of_a_known_user_reads_as_their_name_and_an_unknown_one_stays(fake_slack):
    """Slack sends "@reeve hi" as `<@U…> hi`; a waiting card or a transcript never shows the id."""
    fake_slack.add_user("U1", "alice", display_name="Alice Display")
    fake_slack.add_user("URVE99", "reeve", display_name="reeve")
    fake_slack.add_channel("C1", "general", is_im=False)
    async with _connected(fake_slack) as (_, heard):
        event = await heard.next(fake_slack, channel="C1", user="U1", text="<@URVE99> hi — ask <@UGHOST99> too")
    assert event.text == "@reeve hi — ask <@UGHOST99> too"


async def test_a_reply_is_posted_as_plain_text_in_its_thread(fake_slack):
    fake_slack.add_channel("C1", "general")
    sent = await _send(SlackAdapter("xoxb-test", "xapp-test"), "C1", "outbound text", thread_id="1700000000.000100")
    assert sent.ok is True and sent.message_id
    [posted] = fake_slack.outbound()
    assert posted["method"] == "chat.postMessage"
    assert (posted["channel"], posted["text"], posted["thread_ts"]) == ("C1", "outbound text", "1700000000.000100")
    assert not posted["blocks"]


async def test_the_fakes_reset_route_starts_it_clean(fake_slack):
    control = fake_slack.control_url
    async with httpx.AsyncClient() as client:
        assert (await client.get(f"{control}/health")).json()["ok"] is True
        await client.post(f"{control}/users", json={"id": "U1", "name": "alice"})
        await client.post(f"{control}/channels", json={"id": "C1", "name": "general"})
        assert fake_slack.users and fake_slack.channels
        assert (await _send(SlackAdapter("xoxb-test", "xapp-test"), "C1", "hi")).ok is True
        assert len((await client.get(f"{control}/outbound")).json()["outbound"]) == 1
        assert (await client.post(f"{control}/reset")).json()["ok"] is True
        assert (await client.get(f"{control}/outbound")).json()["outbound"] == []
        assert fake_slack.users == {} and fake_slack.channels == {}
        gone = await client.get(f"{control.replace('/control', '/api')}/conversations.info?channel=C1")
        assert gone.json() == {"ok": False, "error": "channel_not_found"}


async def test_real_bolt_takes_the_fakes_event_and_interaction_envelopes(fake_slack):
    """Guards the envelopes the fake sends against drifting from what slack_bolt reads."""
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp
    from slack_sdk.web.async_client import AsyncWebClient

    fake_slack.add_channel("C1", "general")
    app = AsyncApp(client=AsyncWebClient(token="xoxb-test", base_url=fake_slack.api_url))
    seen: dict = {}
    got_event, got_action = asyncio.Event(), asyncio.Event()

    @app.event("message")
    async def on_event(event, say):  # noqa: ANN001
        seen["event"] = event
        got_event.set()

    @app.action(re.compile(r"^rv_"))
    async def on_action(ack, body):  # noqa: ANN001
        await ack()
        seen["action"] = body
        got_action.set()

    handler = AsyncSocketModeHandler(app, "xapp-test")
    running = asyncio.create_task(handler.start_async())
    try:
        await fake_slack.wait_socket()
        await fake_slack.inbound(channel="C1", user="U1", text="guard event")
        await asyncio.wait_for(got_event.wait(), timeout=5)
        assert (seen["event"]["type"], seen["event"]["text"], seen["event"]["channel"]) == ("message", "guard event", "C1")
        await fake_slack.interaction(channel="C1", user="U1", username="alice", message_ts="1700000001.000001", action_id="rv_0", value="guard-value")
        await asyncio.wait_for(got_action.wait(), timeout=5)
        action = seen["action"]["actions"][0]
        assert seen["action"]["type"] == "block_actions" and (action["action_id"], action["value"]) == ("rv_0", "guard-value")
    finally:
        await handler.close_async()
        running.cancel()


async def test_a_socket_that_dies_without_a_word_is_opened_again(fake_slack):
    """The stall that lasted hours: start_async() sleeps for ever, so a socket that died while the
    client had given up on it (is_connected() down, and staying down) looked alive and nothing
    brought it back. The watchdog asks is_connected() itself and opens a new endpoint. slack_sdk
    recovers from a clean close by the server on its own; the case it gives up on, is_connected()
    stuck at False, is the one played here."""
    fake_slack.add_user("U1", "alice", display_name="Alice")
    fake_slack.add_channel("C1", "general", is_im=False)
    async with _connected(fake_slack, watchdog_interval=0.2) as (adapter, heard):
        assert (await heard.next(fake_slack, channel="C1", user="U1", text="before")).text == "before"
        assert fake_slack.socket_connections == 1
        socket_client = adapter._socket.client
        alive = socket_client.is_connected
        socket_client.is_connected = lambda: False  # down, and the client not reviving it
        waited = 0
        while adapter._reconnects < 1 and waited < 100:
            await asyncio.sleep(0.05)
            waited += 1
        socket_client.is_connected = alive
        assert adapter._reconnects >= 1, "the watchdog never opened the socket again"
        assert fake_slack.socket_connections >= 2
        assert (await heard.next(fake_slack, channel="C1", user="U1", text="after")).text == "after"  # and messages come in again
