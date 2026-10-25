"""Channel subscriptions: a conversation following a Slack channel. What a person types as a
channel, the store of who follows what, the channel's recent messages, the agent's tools, how an
inbound message reaches its followers, and a message from someone not yet allowed.

No Slack: inbound messages are `MessageEvent`s handed to the manager, and delivery is recorded.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from harness.integrations.connectors.messaging import MessageEvent, SessionSource
from harness.integrations.subscriptions import ChannelBuffer, SubscriptionStore, resolve_channel, subscription_tools
from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NoModel(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, *, slack=False):
    manager = SessionManager(workspace=tmp_path, provider=_NoModel())
    if slack:
        # Inbound messages are handled only for a connected Slack; each test connects its own.
        manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    return manager


def _recording(manager, monkeypatch) -> list[tuple[str, str]]:
    delivered: list[tuple[str, str]] = []

    async def deliver(session_id, message, *, source=None):
        delivered.append((session_id, message))

    monkeypatch.setattr(manager, "deliver_to_session", deliver)
    return delivered


def _inbound(text, *, chat_type, chat_id="C1", user="bob", user_id=None):
    return MessageEvent(text=text, source=SessionSource(platform="slack", chat_id=chat_id, user_name=user, chat_type=chat_type, **({"user_id": user_id} if user_id else {})))


# -- naming a channel ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "channel"),
    [
        ("<#C0123|alerts>", "slack:C0123"),  # a Slack #mention
        ("slack:C0999", "slack:C0999"),  # already an address
        ("C0777", "slack:C0777"),  # a bare id, on the default platform
        ("https://acme.slack.com/archives/C0123ABC", "slack:C0123ABC"),  # "Copy link"
        ("https://acme.slack.com/archives/c0123abc?foo=1", "slack:C0123ABC"),
        ("", ""),
        # A #name cannot be looked up here, and taken as it is it would never match the
        # `slack:C…` messages that arrive, so it resolves to nothing.
        ("#general", ""),
    ],
)
def test_what_a_person_types_as_a_channel_resolves_to_its_address(typed, channel):
    assert resolve_channel(typed) == channel


def test_following_a_channel_by_its_name_is_refused_with_what_to_give_instead(tmp_path):
    manager = _manager(tmp_path)
    refused = TestClient(create_app(manager)).post("/v1/subscriptions", json={"session_id": "sN", "channel": "#general"}).json()
    assert refused["ok"] is False and "channel ID" in refused["error"]
    assert manager.subscriptions.for_session("sN") == []


# -- the store and the buffer --------------------------------------------------------------------


def test_who_follows_what_is_kept_once_each_saved_and_removed_with_the_conversation(tmp_path):
    path = tmp_path / "subs.json"
    store = SubscriptionStore(path)
    for session, channel in (("s1", "slack:C1"), ("s2", "slack:C1"), ("s1", "slack:C2"), ("s1", "slack:C1")):
        store.subscribe(session, channel)
    assert {s.session_id for s in store.for_channel("slack:C1")} == {"s1", "s2"}  # the repeat added nothing
    assert {s.channel for s in store.for_session("s1")} == {"slack:C1", "slack:C2"}
    assert {(s.session_id, s.channel) for s in SubscriptionStore(path).all()} == {("s1", "slack:C1"), ("s2", "slack:C1"), ("s1", "slack:C2")}
    assert store.unsubscribe("s2", "slack:C1") is True and store.unsubscribe("s2", "slack:C1") is False
    store.remove_session("s1")  # the one teardown that happens by itself
    assert store.all() == []


def test_a_channels_recent_messages_and_name_survive_a_restart(tmp_path):
    path = tmp_path / "channels.json"
    buffer = ChannelBuffer(state_path=path)
    buffer.record("slack:C9", "bob", "deploy failed", name="ops-alerts")
    buffer.record("slack:C7", "amy", "standup at 10")
    reopened = ChannelBuffer(state_path=path)
    names = {c["channel"]: c["name"] for c in reopened.channels()}
    assert names == {"slack:C9": "ops-alerts", "slack:C7": None}
    assert reopened.recent("slack:C9") == [{"from": "bob", "text": "deploy failed"}]
    # The first format saved was the bare messages dict, and it still loads.
    path.write_text(json.dumps({"slack:C1": [{"from": "z", "text": "old format"}]}))
    assert ChannelBuffer(state_path=path).recent("slack:C1") == [{"from": "z", "text": "old format"}]
    # A file that cannot be read starts empty instead of stopping the app.
    path.write_text("{not json")
    assert ChannelBuffer(state_path=path).channels() == []


def test_the_agent_follows_reads_and_leaves_a_channel(tmp_path):
    buffer = ChannelBuffer()
    follow, leave, following, recent = subscription_tools(SubscriptionStore(tmp_path / "subs.json"), "sess", buffer)
    # Nothing is posted to a channel any more, so following one collides with nothing.
    assert follow("<#C0123|alerts>") == {"ok": True, "subscribed": "slack:C0123"}
    assert following()["channels"] == ["slack:C0123"]
    buffer.record("slack:C0123", "bob", "deploy failed")
    buffer.record("slack:C0123", "sue", "rolling back")
    assert [m["text"] for m in recent("slack:C0123", 5)["messages"]] == ["deploy failed", "rolling back"]
    assert leave("slack:C0123")["was_subscribed"] is True and "slack:C0123" not in following()["channels"]


# -- an inbound message --------------------------------------------------------------------------


def test_a_channel_message_reaches_each_follower_a_dm_its_chosen_conversation(tmp_path, monkeypatch):
    manager = _manager(tmp_path, slack=True)
    delivered = _recording(manager, monkeypatch)
    manager.subscriptions.subscribe("sA", "slack:C1")
    manager.subscriptions.subscribe("sB", "slack:C1")

    def inbound(event):
        asyncio.run(manager._dispatch_inbound(event))

    inbound(_inbound("deploy failed", chat_type="channel"))
    assert {session for session, _ in delivered} == {"sA", "sB"}
    assert manager.channel_buffer.recent("slack:C1")[-1]["text"] == "deploy failed"
    delivered.clear()
    inbound(_inbound("noise", chat_type="channel", chat_id="C2"))  # nobody follows it
    assert delivered == [] and manager.channel_buffer.recent("slack:C2")[-1]["text"] == "noise"
    inbound(_inbound("hi there", chat_type="dm", chat_id="D1"))  # no conversation chosen for DMs
    assert delivered == [] and manager.unrouted.list()[0]["reason"] == "no DM session designated"
    manager.set_dm_session("sDM")
    inbound(_inbound("hello", chat_type="dm", chat_id="D1"))
    assert delivered[-1][0] == "sDM"


def test_a_message_from_someone_not_allowed_waits_and_can_be_dismissed_or_let_through(tmp_path, monkeypatch):
    """Dropped by the allow-list, the message is kept; letting its sender in can also
    deliver it, through the ordinary inbound path, with no resend."""
    from harness.integrations.connectors import Gateway
    from harness.integrations.connectors.config import ConnectorSettings

    manager = _manager(tmp_path, slack=True)
    delivered = _recording(manager, monkeypatch)
    manager.subscriptions.subscribe("sA", "slack:C1")
    gateway = Gateway(secrets=manager.secrets, settings={"slack": ConnectorSettings(platform="slack", enabled=True)}, handler=manager._dispatch_inbound, on_unauthorized=manager._park_unauthorized)
    event = _inbound("deploy failed", chat_type="channel", user_id="U9")
    asyncio.run(gateway._on_inbound(event))
    [kept] = manager.parked.list("slack")
    assert delivered == [] and (kept["text"], kept["user_id"], kept["chat_id"]) == ("deploy failed", "U9", "C1")

    def resolve(item_id, how):
        return asyncio.run(manager.resolve_unauthorized("slack", item_id, how))

    asyncio.run(gateway._on_inbound(event))  # a second copy, to dismiss
    assert resolve(manager.parked.list("slack")[0]["id"], "dismiss")["ok"] and len(manager.parked.list("slack")) == 1
    assert resolve(manager.parked.list("slack")[0]["id"], "allow_deliver")["ok"]
    assert "U9" in manager.secrets.get("slack:default")["allowed_users"]
    assert [session for session, _ in delivered] == ["sA"]
    assert manager.channel_buffer.recent("slack:C1")[-1]["text"] == "deploy failed" and manager.parked.list("slack") == []
    # The sender's name was kept too, so the allow-list shows a name and not an id.
    slack = next(c for c in manager.list_connectors() if c["name"] == "slack")
    assert slack["allowed_user_names"] == {"U9": "bob"}
    assert resolve("nope", "dismiss")["ok"] is False


def test_waiting_messages_are_kept_newest_first_up_to_a_cap_and_survive_a_restart(tmp_path):
    from harness.integrations.connectors.messaging import ParkedStore

    path = tmp_path / "parked.json"
    store = ParkedStore(path, cap=2)
    for text in ("one", "two", "three"):  # the third pushes out the first
        store.park(platform="slack", chat_id="C1", user_id="U1", text=text)
    assert [item["text"] for item in store.list("slack")] == ["three", "two"]
    reopened = ParkedStore(path, cap=2)
    assert [item["text"] for item in reopened.list()] == ["three", "two"]
    taken = reopened.pop(reopened.list()[0]["id"])
    assert taken is not None and taken.text == "three"
    assert ParkedStore(path, cap=2).list() == [{**item} for item in reopened.list()]


def test_new_tokens_take_effect_by_replacing_the_gateway_without_a_restart(tmp_path):
    manager = _manager(tmp_path)
    asyncio.run(manager.start_gateway())
    first = manager.gateway
    assert first is not None
    asyncio.run(manager.refresh_gateway())
    assert manager.gateway is not None and manager.gateway is not first
    asyncio.run(manager.aclose())


# -- the routes ----------------------------------------------------------------------------------


def test_the_subscriptions_list_names_each_channel_and_every_session_row_lists_its_own(tmp_path):
    manager = _manager(tmp_path)
    manager.subscriptions.subscribe("s1", "slack:C1")
    manager.channel_buffer.record("slack:C1", "bob", "hello", name="ops-alerts")
    client = TestClient(create_app(manager))
    [row] = client.get("/v1/subscriptions").json()["subscriptions"]
    assert (row["session_id"], row["channel"], row["channel_name"]) == ("s1", "slack:C1", "ops-alerts")
    assert "collision" not in row and "routing_target" not in row
    assert all("subscriptions" in s for s in client.get("/v1/sessions").json()["sessions"])


def test_a_recent_channel_is_offered_followed_by_its_mention_and_left(tmp_path):
    manager = _manager(tmp_path)
    manager.channel_buffer.record("slack:C9", "bob", "deploy failed")  # what the picker offers
    client = TestClient(create_app(manager))
    assert [c["channel"] for c in client.get("/v1/channels/recent").json()["channels"]] == ["slack:C9"]
    followed = client.post("/v1/subscriptions", json={"session_id": "sZ", "channel": "<#C9|alerts>"}).json()
    assert followed["ok"] and followed["channel"] == "slack:C9"
    assert [s.channel for s in manager.subscriptions.for_session("sZ")] == ["slack:C9"]
    left = client.post("/v1/subscriptions/remove", json={"session_id": "sZ", "channel": "slack:C9"}).json()
    assert left["ok"] and left["removed"] is True and manager.subscriptions.for_session("sZ") == []
