"""The UI refresh, end to end over FakeSlack: the one scenario that crosses all its phases.

One scenario runs phases 1 to 4 together through the real Slack adapter and slack_bolt, against
FakeSlack (no network, no tokens, no Slack app):

1. Slack is connected through the manager's gateway, a sender allowed, and an Ops "incident"
   conversation set to follow a channel.
2. A message posted there reaches the conversation as a connector message: the saved message carries
   a sidecar with the channel's and the sender's names looked up (phase 2), the live turn_start
   carries the same, and the model is sent the framed text without it.
3. The agent asks to write a file. The approval waits for the person and nothing is posted to the
   channel; answered the way the app answers it, the turn goes on and replies in the channel.
4. Slack muted for the conversation, another post does not wake it, and is still kept in the buffer.
5. Nothing recommends a connector any more.

`REEVE_STATE_DIR` and the manager's data folder are under `tmp_path`, so the machine's own secrets
and settings are never touched.
"""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.sessions import SessionRecord

SID = "incident"
CHANNEL, CHANNEL_NAME = "C_OPS", "ops-incidents"
USER, SENDER_DISPLAY = "U1", "Alice Display"
ALERT = "\U0001f6a8 deploy to prod failed at 14:03"
REPLY = "Acknowledged - investigating the deploy now."


class _Scripted(ProviderClient):
    """Answers with the queued turns and keeps every request's messages."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _calls(name, arguments, call_id):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])


async def _eventually(check, *, timeout: float = 8.0):
    """`check()` once it is true, or its last answer when the time runs out."""
    deadline = time.monotonic() + timeout
    answer = check()
    while not answer and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
        answer = check()
    return answer


def _posted_cards(outbound):
    return [o for o in outbound if o["method"] == "chat.postMessage" and o.get("blocks")]


def _posted_replies(outbound, text):
    return [o for o in outbound if o["method"] == "chat.postMessage" and not o.get("blocks") and o["channel"] == CHANNEL and o["text"] == text]


async def _connected_and_following(fake_slack, manager, workspace):
    manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    assert "slack" in await manager.start_gateway()  # the real adapter, reaching FakeSlack
    await fake_slack.wait_socket()
    fake_slack.add_user(USER, "alice", display_name=SENDER_DISPLAY)  # for users.info
    fake_slack.add_channel(CHANNEL, CHANNEL_NAME)  # for conversations.info
    assert manager.allow_user("slack", USER)["ok"] is True  # unknown senders are dropped
    manager.session_store.save(SessionRecord(session_id=SID, workspace=str(workspace), model="gpt-5.5", mode="interactive", agent="ops"))
    manager.subscriptions.subscribe(SID, f"slack:{CHANNEL}")
    engine = manager.get_engine(SID)
    assert engine is not None
    engine.permissions.allow_tool_for_session("send_message")  # so the reply itself does not ask


def _assert_the_message_arrived_with_its_names(client, provider, frames):
    saved = [m for m in client.get(f"/v1/sessions/{SID}/messages").json()["messages"] if m.get("role") == "user"]
    assert saved
    card = saved[-1]["source"]
    assert (card["connector"], card["kind"], card["channel_id"], card["sender_id"]) == ("slack", "channel", CHANNEL, USER)
    assert card["channel_name"] == CHANNEL_NAME and card["sender_name"] == SENDER_DISPLAY  # looked up (phase 2)
    assert card["text"] == ALERT  # the card shows the message as it was sent
    assert "subscribed" in saved[-1]["content"] and saved[-1]["content"] != ALERT  # the model reads the framed text
    assert provider.calls
    sent = [m for m in provider.calls[0] if m.get("role") == "user"]
    assert sent and "source" not in sent[-1] and "subscribed" in sent[-1]["content"]
    assert all("source" not in m for call in provider.calls for m in call)
    starts = [f for f in frames if f.get("type") == "turn_start"]
    assert starts, frames
    live = starts[0]["data"]["source"]
    assert (live["channel_name"], live["sender_name"], live["text"]) == (CHANNEL_NAME, SENDER_DISPLAY, ALERT)


async def test_ui_refresh_cross_cutting_e2e(fake_slack, tmp_path, monkeypatch):
    # The secret store decides "is Slack connected?" from what this test writes, nothing else.
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "ops_ws"
    workspace.mkdir()
    provider = _Scripted([
        _calls("write_file", {"path": str(workspace / "incident-note.txt"), "content": "ok"}, "call_w"),  # waits for the person
        _calls("send_message", {"target": f"slack:{CHANNEL}", "text": REPLY}, "call_s"),  # allowed beforehand
        AssistantTurn(text="Posted the acknowledgement to the channel.", finish_reason="stop"),
    ])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    try:
        await _connected_and_following(fake_slack, manager, workspace)
        frames: list[dict] = []

        async def watch(frame):
            frames.append(frame)

        manager.register_session_client(SID, watch)
        client = TestClient(create_app(manager))  # not entered, so no second gateway is started

        # A post in the channel becomes a connector message, and the agent's write waits.
        await fake_slack.inbound(channel=CHANNEL, user=USER, text=ALERT)
        waiting = await _eventually(lambda: manager.inbox.pending(SID))
        assert waiting, "the approval was never parked"
        approval = waiting[0]
        _assert_the_message_arrived_with_its_names(client, provider, frames)
        assert approval.kind == "approval" and approval.state == "pending" and "write_file" in approval.title
        row = next(r for r in manager.list_sessions() if r["session_id"] == SID)
        assert row["attention"] == 1 and row["attention_kind"] == "approval"
        assert _posted_cards(fake_slack.outbound()) == [] and fake_slack.outbound() == []

        # Answered as the app answers it: the turn goes on and replies in the channel.
        assert client.post(f"/v1/inbox/{approval.id}/resolve", json={"resolution": "allow"}).json()["ok"] is True
        assert await _eventually(lambda: _posted_replies(fake_slack.outbound(), REPLY)), f"reply never posted back: {fake_slack.outbound()}"
        assert await _eventually(lambda: not manager.is_running(SID))
        assert manager.inbox.get(approval.id).state == "resolved"

        # Muted, the conversation is not woken by the next post, which is still kept.
        # The title written after the turn calls the model on a worker thread; it settles first,
        # so its call cannot land between the counts below.
        assert await _eventually(lambda: SID not in manager._autotitle_inflight)
        messages_before, calls_before = len(manager.session_messages(SID)), len(provider.calls)
        assert client.post(f"/v1/sessions/{SID}/connections", json={"connector": "slack", "enabled": False}).json()["ok"] is True
        assert "slack" not in manager.effective_connectors(SID)
        muted = "second alert while muted"
        await fake_slack.inbound(channel=CHANNEL, user=USER, text=muted)
        assert await _eventually(lambda: any(m["text"] == muted for m in manager.channel_buffer.recent(f"slack:{CHANNEL}"))), "muted message was not buffered"
        await asyncio.sleep(0.1)  # the decision not to deliver is made with the buffering; this guards a stray one
        assert len(manager.session_messages(SID)) == messages_before and len(provider.calls) == calls_before
        assert not manager.is_running(SID)

        # One agent, and nothing recommends a connector.
        view = client.get(f"/v1/sessions/{SID}/connections").json()
        assert view["attention"] == 0 and view["recommended"] == []
    finally:
        await manager.aclose()
    # The secret store was the tmp_path one, never the machine's own.
    assert str(tmp_path) in str(manager.secrets.path)
