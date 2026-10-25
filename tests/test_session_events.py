"""A conversation's own stream of events: a turn that starts with nobody's socket behind it (a
channel message, a self-wake, a resume) sends its events to every socket viewing that conversation,
and only to those. The delivery does not depend on a socket being there, and a turn that fails with
nobody to read the error is kept where it can be found.
"""

import asyncio

from fastapi.testclient import TestClient

from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient


class _Says(ProviderClient):
    def __init__(self, *texts):
        self._texts = list(texts)

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text=self._texts.pop(0), finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _Dead(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise RuntimeError("model is dead (401)")

    def capabilities(self, model):
        return ModelCapabilities()


class _Viewer:
    """A socket on the conversation: keeps every frame sent to it."""

    def __init__(self):
        self.frames: list[dict] = []

    async def __call__(self, frame):
        self.frames.append(frame)

    @property
    def kinds(self):
        return [frame["type"] for frame in self.frames]


def _conversation(tmp_path, provider):
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.get_engine("S", agent="chat")  # a saved conversation with no workspace
    return manager


def _watching(manager, sid="S"):
    viewer = _Viewer()
    manager.register_session_client(sid, viewer)
    return viewer


def _deliver(manager, text):
    asyncio.run(manager.deliver_to_session("S", text))


def test_a_delivered_message_streams_the_whole_turn_to_the_viewer(tmp_path):
    manager = _conversation(tmp_path, _Says("hello"))
    viewer = _watching(manager)
    _deliver(manager, "hi")
    assert viewer.kinds[0] == "turn_start" and viewer.frames[0]["data"]["input"] == "hi"  # shown as the person's message
    assert "assistant_message" in viewer.kinds and viewer.kinds[-1] == "turn_done"


def test_every_viewer_gets_it_and_one_that_left_does_not(tmp_path):
    manager = _conversation(tmp_path, _Says("yo", "b", "c"))
    first, second = _watching(manager), _watching(manager)
    _deliver(manager, "hi")
    assert "turn_done" in first.kinds and "turn_done" in second.kinds
    first.frames.clear()
    second.frames.clear()
    manager.unregister_session_client("S", first)
    _deliver(manager, "two")
    assert first.frames == [] and "turn_done" in second.kinds
    manager.unregister_session_client("S", second)  # the last one leaving
    second.frames.clear()
    _deliver(manager, "three")
    assert second.frames == []


def test_a_viewer_of_another_conversation_sees_nothing(tmp_path):
    manager = _conversation(tmp_path, _Says("x"))
    elsewhere = _watching(manager, "OTHER")
    _deliver(manager, "hi")
    assert elsewhere.frames == []


def test_a_background_turn_that_fails_is_parked_not_lost(tmp_path):
    # with nobody to read the error event, it would be gone; the dead-letter store keeps it
    manager = _conversation(tmp_path, _Dead())
    viewer = _watching(manager)
    _deliver(manager, "do the thing")  # and nothing is raised
    assert "error" in viewer.kinds
    [parked] = manager.unrouted.list()
    assert (parked["source"], parked["text"]) == ("S", "do the thing")
    assert "dead" in parked["reason"] or "401" in parked["reason"]


def test_what_is_parked_is_served_to_the_app(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Says())
    manager.unrouted.record("slack:D1", "bob", "hey", reason="no DM session designated")
    [item] = TestClient(create_app(manager)).get("/v1/unrouted").json()["items"]
    assert (item["source"], item["reason"]) == ("slack:D1", "no DM session designated")
