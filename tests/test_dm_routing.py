"""Where a direct message goes: to the conversation the person chose for them, delivered like any
background turn, or, with none chosen, parked as unrouted. The old super-agent that once took them is
gone, routes and all.
"""

import asyncio

from fastapi.testclient import TestClient

from harness.integrations.connectors.messaging import MessageEvent, SessionSource
from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NoTurns(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, **options):
    return SessionManager(workspace=tmp_path, provider=_NoTurns(), **options)


def _direct(text):
    return MessageEvent(text=text, source=SessionSource(platform="slack", chat_id="D1", user_name="bob", chat_type="dm"))


def test_a_direct_message_reaches_the_chosen_conversation(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    # a message is only taken in from a connected Slack; with the state folder of its own
    # (conftest) each test connects one, where once the developer's real profile did it
    manager.secrets.put("slack:default", {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True})
    reached: list[tuple[str, str]] = []

    async def deliver(session_id, message, *, source=None):
        reached.append((session_id, message))

    monkeypatch.setattr(manager, "deliver_to_session", deliver)
    manager.set_dm_session("sDM")
    asyncio.run(manager._dispatch_inbound(_direct("ping")))
    [(to, text)] = reached
    assert to == "sDM" and "ping" in text  # the message, framed with a way to reply
    assert manager.unrouted.list() == []


def test_with_no_conversation_chosen_it_is_parked(tmp_path):
    manager = _manager(tmp_path)
    assert manager.dm_session() is None
    asyncio.run(manager._dispatch_inbound(_direct("hello there")))
    [parked] = manager.unrouted.list()
    assert (parked["text"], parked["reason"]) == ("hello there", "no DM session designated")


def test_the_choice_is_set_read_and_cleared_through_the_api(tmp_path):
    client = TestClient(create_app(_manager(tmp_path)))

    def chosen():
        return client.get("/v1/messaging/dm-route").json()["dm_session"]

    def choose(sid):
        return client.post("/v1/messaging/dm-route", json={"session_id": sid}).json()["dm_session"]

    assert chosen() is None
    assert choose("sX") == "sX" and chosen() == "sX"
    assert choose("") is None  # an empty id clears it


def test_the_choice_survives_a_restart(tmp_path):
    manager = _manager(tmp_path)
    manager.set_dm_session("sKeep")
    assert _manager(tmp_path, data_dir=manager._data_base).dm_session() == "sKeep"  # read back from the prefs


def test_the_super_agent_is_gone(tmp_path):
    manager = _manager(tmp_path)
    assert not hasattr(manager, "superagent") and not hasattr(manager, "sa_register")
    assert TestClient(create_app(manager)).get("/v1/superagent").status_code == 404
