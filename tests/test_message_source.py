"""Where a message came from: a connector's message reaches a conversation as framed
text the model reads, plus a `MessageSource` sidecar the transcript draws as a card. The sidecar is
saved, so the card survives a reload, and no provider is ever sent it. The same holds for a tool
result's `_display` sidecar.
"""

import asyncio

from fastapi.testclient import TestClient

from harness.integrations.connectors.messaging import MessageEvent, SessionSource
from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.tools import ToolRegistry

SENT_AT = "1700000001.000001"


class _Keeps(ProviderClient):
    """Answers with the queued turns and keeps a copy of every request's messages."""

    def __init__(self, turns=()):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _channel_message(text="deploy failed"):
    return MessageEvent(text=text, source=SessionSource(platform="slack", chat_id="C1", user_id="U1", user_name="Bob", chat_name="#reeve-test", chat_type="channel"), message_id=SENT_AT)


def _manager(tmp_path, provider):
    manager = SessionManager(workspace=tmp_path, provider=provider)
    # Inbound messages are handled only for a connected Slack; each test connects its own.
    manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    return manager


def _delivered(manager, monkeypatch):
    delivered: list[tuple] = []

    async def deliver(session_id, message, *, source=None):
        delivered.append((session_id, message, source))

    monkeypatch.setattr(manager, "deliver_to_session", deliver)
    return delivered


def _engine(tmp_path, **fields):
    return TurnEngine(provider=_Keeps(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5", **fields)


# -- the sidecar ---------------------------------------------------------------------------------


def test_a_channel_message_is_delivered_framed_with_a_sidecar_naming_where_it_came_from(tmp_path, monkeypatch):
    manager = _manager(tmp_path, _Keeps())
    delivered = _delivered(manager, monkeypatch)
    manager.subscriptions.subscribe("sA", "slack:C1")
    asyncio.run(manager._dispatch_inbound(_channel_message()))
    [(to, message, source)] = delivered
    assert to == "sA"
    assert source == {
        "connector": "slack", "kind": "channel", "channel_id": "C1", "channel_name": "#reeve-test",
        "sender_id": "U1", "sender_name": "Bob", "ts": float(SENT_AT), "text": "deploy failed",
    }
    assert "subscribed" in message and "deploy failed" in message  # the framed text, not the bare message


def test_a_direct_message_is_marked_as_one_and_reaches_the_dm_conversation(tmp_path, monkeypatch):
    manager = _manager(tmp_path, _Keeps())
    delivered = _delivered(manager, monkeypatch)
    manager.set_dm_session("sDM")
    asyncio.run(manager._dispatch_inbound(MessageEvent(text="ping me", source=SessionSource(platform="slack", chat_id="D1", user_id="U2", user_name="Sue", chat_name="Sue", chat_type="dm"), message_id="1700000002.000002")))
    [(to, _, source)] = delivered
    assert to == "sDM"
    assert (source["kind"], source["connector"], source["channel_id"], source["sender_name"], source["text"]) == ("dm", "slack", "D1", "Sue", "ping me")


def test_the_sidecar_is_saved_for_the_card_and_never_sent_to_the_model(tmp_path):
    provider = _Keeps([AssistantTurn(text="ack", finish_reason="stop")])
    manager = _manager(tmp_path, provider)
    manager.get_engine("S", agent="chat")  # a saved conversation with no workspace
    manager.subscriptions.subscribe("S", "slack:C1")
    asyncio.run(manager._dispatch_inbound(_channel_message()))
    stored = [m for m in TestClient(create_app(manager)).get("/v1/sessions/S/messages").json()["messages"] if m.get("role") == "user"][-1]
    card = stored["source"]
    assert (card["connector"], card["channel_name"], card["sender_name"], card["text"]) == ("slack", "#reeve-test", "Bob", "deploy failed")
    assert "subscribed" in stored["content"] and stored["content"] != "deploy failed"  # the model reads the framed text
    assert provider.calls
    sent = [m for m in provider.calls[0] if m.get("role") == "user"][-1]
    assert "source" not in sent and "subscribed" in sent["content"]
    assert all("source" not in m for call in provider.calls for m in call)


def test_the_sidecar_is_left_out_of_what_is_sent_with_or_without_a_live_block(tmp_path):
    card = {"connector": "slack", "kind": "channel", "text": "raw"}
    plain = _engine(tmp_path)
    plain.messages.append({"role": "user", "content": "framed", "source": card})
    assert all("source" not in m for m in plain._outbound_messages())
    assert plain.messages[-1]["source"] == card  # kept on the message itself
    live = _engine(tmp_path, context_provider=lambda: "live ctx")
    live.messages.append({"role": "user", "content": "framed", "source": card})
    live._stamp_live_context(live.messages[-1])
    sent = live._outbound_messages()
    assert all("source" not in m for m in sent) and "<system-context>" in sent[-1]["content"]
    assert live.messages[-1]["source"] == card


def test_the_turn_start_frame_carries_the_sidecar(tmp_path):
    manager = _manager(tmp_path, _Keeps([AssistantTurn(text="ok", finish_reason="stop")]))
    manager.get_engine("S", agent="chat")
    manager.subscriptions.subscribe("S", "slack:C1")
    frames: list[dict] = []

    async def client(frame):
        frames.append(frame)

    manager.register_session_client("S", client)
    asyncio.run(manager._dispatch_inbound(_channel_message()))
    starts = [f for f in frames if f["type"] == "turn_start"]
    assert starts, frames
    card = starts[0]["data"]["source"]
    assert (card["connector"], card["kind"], card["channel_name"], card["text"]) == ("slack", "channel", "#reeve-test", "deploy failed")


def test_a_tool_results_display_sidecar_is_for_the_card_and_the_audit_and_not_the_model(tmp_path):
    """The model sees no placeholder for what a filter hid (a Gmail search, say); the card shows
    the count and the audit names the rule."""
    audits: list[dict] = []
    engine = _engine(tmp_path, audit_sink=audits.append)
    hidden = {"hidden_by_filters": 2, "connector": "gmail"}
    engine._record_result(ToolCall(id="t1", name="gmail_search_messages", arguments={"query": "q"}), {"ok": True, "data": {"messages": [{"id": "m2"}]}, "_display": hidden}, "ok")
    message = engine.messages[-1]
    assert message["_display"] == hidden and "_display" not in message["content"] and "hidden" not in message["content"]
    assert all("_display" not in m for m in engine._outbound_messages()) and engine.messages[-1]["_display"]
    [filtered] = [row for row in audits if row.get("stage") == "filtered"]
    assert "2 result(s) hidden" in filtered["reason"]
