"""When each message was written (the server's half): a `ts` in unix seconds is put on every
message as it joins the history, saved with it, and served as it is by
`GET /v1/sessions/{id}/messages`. Like `source` and `_display` it is for the surfaces and is never in
what a provider is sent.
"""

from __future__ import annotations

import asyncio
import time

import aisuite as ai
from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.tools import ToolRegistry

STAMP = 1700000000.0


class _Keeps(ProviderClient):
    """Answers with the queued turns and keeps a copy of each request's messages."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _reads_then_says(tmp_path, said):
    """An engine with the file tools whose model reads a.txt, then answers."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = _Keeps([AssistantTurn(tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})], finish_reason="tool_calls"), AssistantTurn(text=said, finish_reason="stop")])
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(provider=provider, registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5"), provider


def _run(engine, text):
    async def run():
        return [event async for event in engine.run(text)]

    return asyncio.run(run())


def _has_no_ts(value) -> bool:
    if isinstance(value, dict):
        return all(key != "ts" and _has_no_ts(inner) for key, inner in value.items())
    if isinstance(value, list):
        return all(_has_no_ts(inner) for inner in value)
    return True


def test_every_message_a_turn_adds_is_stamped_with_when(tmp_path):
    engine, _ = _reads_then_says(tmp_path, "it says hello")
    began = time.time()
    _run(engine, "read a.txt")
    ended = time.time()
    assert [m["role"] for m in engine.messages] == ["user", "assistant", "tool", "assistant"]
    for message in engine.messages:  # the tool's result too
        assert isinstance(message["ts"], float) and began <= message["ts"] <= ended


def test_no_request_carries_the_stamp_and_the_history_keeps_it(tmp_path):
    engine, provider = _reads_then_says(tmp_path, "done")
    _run(engine, "read a.txt")
    assert provider.calls, "the provider was never called"
    assert all("ts" not in m for call in provider.calls for m in call)
    assert all("ts" in m for m in engine.messages)  # left out of a copy, not taken off


def test_what_is_sent_never_has_the_stamp_even_with_nothing_else_to_add(tmp_path):
    # the one path every request is built by, including its early return with no live context
    engine = TurnEngine(provider=_Keeps([]), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5")
    engine.messages.append({"role": "user", "content": "hi", "ts": STAMP})
    assert all("ts" not in m for m in engine._outbound_messages())
    assert engine.messages[-1]["ts"] == STAMP


def test_the_native_providers_build_their_payload_without_it():
    """A second guard: the Anthropic and Gemini payloads are rebuilt from role and content, so a
    stamp that got past the engine still never reaches the wire."""
    from harness.llm.providers.claude.convert import convert_messages as to_anthropic
    from harness.llm.providers.gemini.convert import convert_messages as to_gemini

    history = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi", "ts": STAMP}, {"role": "assistant", "content": "hello", "ts": STAMP + 1}]
    for convert in (to_anthropic, to_gemini):
        assert _has_no_ts(convert(history)[1])


def test_the_messages_endpoint_serves_the_stamps(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Keeps([AssistantTurn(text="hi there", finish_reason="stop")]))
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/ts1") as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "user_message", "text": "hello world"})
        while socket.receive_json()["type"] != "turn_done":
            pass
    served = [m for m in client.get("/v1/sessions/ts1/messages").json()["messages"] if m.get("role") in ("user", "assistant")]
    assert served and all(isinstance(m.get("ts"), float) for m in served)
