"""Being tagged in Slack.

Tagging @reeve in a channel is the main way in. With no conversation following the channel, the tag
opens one for its thread: listed in the sidebar, allowed to reply into that thread, and steered by
later tags in the same thread. A channel a conversation follows answers its own tags instead.
Untagged messages are only for a follower to judge, and silence is the default.
"""

import asyncio
import sqlite3

import pytest

from harness.integrations.connectors.messaging import MessageEvent, SessionSource, slack_event_to_event
from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.session.conversations import ConversationStore
from harness.session.sessions import SessionRecord

FIRST_TS = "1700000010.000100"
THREAD = f"slack:C1:{FIRST_TS}"  # a tag at the top level threads on its own message


class _Answers(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _tag(text="<@UBOT> check the deploy?", *, ts=FIRST_TS, thread_ts=None, tagged=True):
    source = SessionSource(platform="slack", chat_id="C1", user_id="U1", user_name="Bob", chat_name="general", chat_type="channel", thread_id=thread_ts)
    return MessageEvent(text=text, source=source, message_id=ts, mentions_me=tagged)


def _manager(tmp_path, monkeypatch):
    manager = SessionManager(workspace=tmp_path, provider=_Answers())
    manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    delivered: list[tuple] = []

    async def deliver(session_id, message, *, source=None):
        delivered.append((session_id, message, source))

    monkeypatch.setattr(manager, "deliver_to_session", deliver)
    return manager, delivered


def _arrives(manager, event):
    asyncio.run(manager._dispatch_inbound(event))


def _slack_sessions(manager):
    return [row for row in manager.list_sessions() if row["origin"] == "slack"]


# -- whether a message tags the bot --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "bot", "tagged"),
    [("<@UBOT> hi", "UBOT", True), ("hey <@UBOT|reeve> hi", "UBOT", True), ("<@UOTHER> hi", "UBOT", False), ("<@UBOT> hi", None, False)],
)
def test_a_message_tags_the_bot_only_when_it_names_the_bots_own_id(text, bot, tagged):
    """Read from Slack's raw text, before mentions are rewritten to names; without a known bot id, never."""
    event = slack_event_to_event({"channel": "C1", "user": "U1", "ts": "1.2", "channel_type": "channel", "text": text}, bot)
    assert bool(event.mentions_me) is tagged


# -- a tag in a channel nobody follows -----------------------------------------------------------


def test_a_tag_opens_a_listed_conversation_for_its_thread_allowed_to_reply_there(tmp_path, monkeypatch):
    manager, delivered = _manager(tmp_path, monkeypatch)
    _arrives(manager, _tag())
    [row] = _slack_sessions(manager)
    # Titled by the ask (the tag itself is noise), with the channel as the tail that may be cut
    # (owner, 2026-07-14).
    assert (row["origin_label"], row["title"]) == ("#general", "check the deploy? — #general")
    session = row["session_id"]
    assert manager.mention_sessions.get(THREAD) == session
    assert THREAD in manager._engines[session].permissions.task_rules["send_message"]
    to, opening, source = delivered[-1]
    assert to == session and THREAD in opening and "pre-approved" in opening
    assert (source["connector"], source["kind"]) == ("slack", "channel")


def test_a_later_tag_in_the_same_thread_steers_the_same_conversation(tmp_path, monkeypatch):
    manager, delivered = _manager(tmp_path, monkeypatch)
    _arrives(manager, _tag())
    session = manager.list_sessions()[0]["session_id"]
    _arrives(manager, _tag("<@UBOT> and staging too", ts="1700000012.000300", thread_ts=FIRST_TS))
    assert len(manager.list_sessions()) == 1
    to, message, _ = delivered[-1]
    assert to == session and "Follow-up" in message and "and staging too" in message


def test_a_tag_in_another_thread_opens_another_conversation(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    _arrives(manager, _tag())
    _arrives(manager, _tag("<@UBOT> other thing", ts="1700000099.000900"))
    assert len({thread.session_id for thread in manager.mention_sessions.all()}) == 2
    assert len(manager.list_sessions()) == 2


def test_the_thread_grant_comes_back_when_the_engine_is_rebuilt(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    _arrives(manager, _tag())
    session = manager.list_sessions()[0]["session_id"]
    manager._engines.pop(session)  # as a restart would
    assert THREAD in manager.get_engine(session).permissions.task_rules["send_message"]


def test_deleting_the_conversation_frees_its_thread_for_a_new_one(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    _arrives(manager, _tag())
    session = manager.list_sessions()[0]["session_id"]
    manager.delete_session(session)
    assert manager.mention_sessions.all() == []
    _arrives(manager, _tag(thread_ts=FIRST_TS))
    [fresh] = manager.list_sessions()
    assert fresh["session_id"] != session


# -- a channel a conversation follows ------------------------------------------------------------


def test_a_tag_in_a_followed_channel_is_answered_by_its_follower_and_opens_nothing(tmp_path, monkeypatch):
    manager, delivered = _manager(tmp_path, monkeypatch)
    manager.subscriptions.subscribe("sA", "slack:C1")
    _arrives(manager, _tag())
    [(to, message, _)] = delivered
    assert to == "sA" and "must" in message and "respond" in message and THREAD in message
    assert manager.mention_sessions.all() == [] and manager.list_sessions() == []


def test_an_untagged_message_is_the_followers_to_judge_and_without_one_goes_nowhere(tmp_path, monkeypatch):
    manager, delivered = _manager(tmp_path, monkeypatch)
    manager.subscriptions.subscribe("sA", "slack:C1")
    _arrives(manager, _tag("lunch anyone?", ts="1700000011.000200", tagged=False))
    _, message, _ = delivered[0]
    assert "subscribed" in message and "stay silent" in message
    delivered.clear()
    manager.subscriptions.unsubscribe("sA", "slack:C1")
    _arrives(manager, _tag("lunch anyone?", ts="1700000013.000400", tagged=False))
    assert delivered == [] and manager.list_sessions() == []  # kept in the buffer only


# -- where a conversation came from --------------------------------------------------------------


def test_where_a_conversation_came_from_is_kept_through_later_saves(tmp_path):
    store = ConversationStore(tmp_path)
    record = SessionRecord(session_id="s1", workspace=str(tmp_path), model="m", mode="interactive")
    store.save(record)
    assert store.set_origin("s1", "slack", "#general · T1")
    loaded = store.load("s1")
    assert (loaded.origin, loaded.origin_label) == ("slack", "#general · T1")
    store.save(record)  # a later save of the turn leaves it alone
    assert store.load("s1").origin == "slack"
    assert {r.session_id: r for r in store.list()}["s1"].origin_label == "#general · T1"


def test_a_database_from_before_origins_gains_the_columns_when_opened(tmp_path):
    connection = sqlite3.connect(tmp_path / "reeve.db")
    connection.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, "
        "mode TEXT, title TEXT, agent TEXT DEFAULT 'reeve', n_msgs INTEGER DEFAULT 0, "
        "messages TEXT, extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0, "
        "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute("INSERT INTO sessions (session_id, workspace, model, mode) VALUES ('old', 'w', 'm', 'i')")
    connection.commit()
    connection.close()
    store = ConversationStore(tmp_path)
    old = store.load("old")
    assert old is not None and old.origin is None
    assert store.set_origin("old", "slack", "#x") and store.load("old").origin == "slack"
