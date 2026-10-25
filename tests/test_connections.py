"""Which connectors a conversation has: a connector is on for it when the account
is connected and the conversation has not switched it off. (The persona layer between the two went
with personas.)

The store, the resolver, and the two places it is acted on: messages coming in, and the connector
tools an engine is built with.
"""

import asyncio

import pytest

from harness.integrations.connections import SessionConnectionStore, effective
from harness.integrations.connectors.messaging import MessageEvent, SessionSource
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.session.sessions import SessionRecord


@pytest.fixture(autouse=True)
def _own_state_dir(tmp_path, monkeypatch):
    # A manager's SecretStore() writes to the state folder; unset, that is the developer's own
    # (~/.config/Reeve), and a test's fake token would land in their real secrets.
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))


class _NoTurns(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path):
    return SessionManager(workspace=tmp_path, provider=_NoTurns())


def _saved(manager, tmp_path, *session_ids):
    for sid in session_ids:
        manager.session_store.save(SessionRecord(session_id=sid, workspace=str(tmp_path), model="gpt-5.5", mode="interactive", agent="reeve"))


def _deliveries(manager, monkeypatch) -> list[str]:
    reached: list[str] = []

    async def deliver(session_id, message, *, source=None):
        reached.append(session_id)

    monkeypatch.setattr(manager, "deliver_to_session", deliver)
    return reached


def _from_slack(text, chat_id, kind, sender):
    return MessageEvent(text=text, source=SessionSource(platform="slack", chat_id=chat_id, user_name=sender, chat_type=kind))


# -- the resolver and the store ------------------------------------------------------------------


def test_on_means_connected_and_not_switched_off_here():
    on = effective(connected={"slack", "github"}, session_overrides={"slack": False, "datadog": True})
    assert on == {"github": True}  # slack is off here; datadog is not connected, and no switch connects it


def test_clearing_a_switch_goes_back_to_on(tmp_path):
    store = SessionConnectionStore(tmp_path / "session_connections.json")
    store.set("s1", "slack", False)
    assert store.get("s1") == {"slack": False}
    assert effective(connected={"slack"}, session_overrides=store.get("s1")) == {}
    store.clear("s1", "slack")
    assert store.get("s1") == {}
    assert effective(connected={"slack"}, session_overrides=store.get("s1")) == {"slack": True}


def test_a_conversation_leaving_takes_its_switches_and_no_one_elses(tmp_path):
    path = tmp_path / "session_connections.json"
    store = SessionConnectionStore(path)
    store.set("s1", "slack", False)
    store.set("s2", "github", False)
    store.remove_session("s1")
    assert store.get("s1") == {} and store.get("s2") == {"github": False}
    assert SessionConnectionStore(path).get("s1") == {}  # and it is gone on disk


def test_deleting_a_conversation_drops_its_switches(tmp_path):
    manager = _manager(tmp_path)
    _saved(manager, tmp_path, "sX")
    manager.session_connections.set("sX", "slack", False)
    manager.delete_session("sX")
    assert manager.session_connections.get("sX") == {}


# -- messages coming in --------------------------------------------------------------------------


def test_a_channel_message_skips_a_conversation_that_switched_slack_off_and_is_still_kept(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    reached = _deliveries(manager, monkeypatch)
    manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    for sid in ("sListen", "sMute"):  # two conversations follow the same channel
        manager.subscriptions.subscribe(sid, "slack:C1")
    manager.session_connections.set("sMute", "slack", False)
    asyncio.run(manager._dispatch_inbound(_from_slack("deploy failed", "C1", "channel", "bob")))
    assert "sListen" in reached and "sMute" not in reached
    assert manager.channel_buffer.recent("slack:C1")[-1]["text"] == "deploy failed"  # there to catch up on


def test_a_direct_message_to_a_conversation_with_slack_off_is_parked(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    reached = _deliveries(manager, monkeypatch)
    manager.set_dm_session("sDM")
    manager.session_connections.set("sDM", "slack", False)
    asyncio.run(manager._dispatch_inbound(_from_slack("ping", "D1", "dm", "sue")))
    assert reached == []
    parked = manager.unrouted.list()
    assert parked and parked[0]["reason"] == "connector muted for DM session"


# -- the tools an engine is built with -----------------------------------------------------------


def test_a_conversation_with_a_connector_off_is_built_without_its_tools(tmp_path):
    manager = _manager(tmp_path)
    manager.secrets.put("jira:default", {"base_url": "https://example.atlassian.net", "email": "me@example.com", "api_token": "tok", "enabled": True})
    _saved(manager, tmp_path, "sOn", "sOff")
    manager.session_connections.set("sOff", "jira", False)
    assert "jira_search_issues" in manager.get_engine("sOn").registry.names()
    assert "jira_search_issues" not in manager.get_engine("sOff").registry.names()
