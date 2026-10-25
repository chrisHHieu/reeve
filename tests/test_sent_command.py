"""A message keeps the slash command it was sent with, as the transcript draws it.

`/init` reaches the model as the template it expands to; the person wrote `/init` and a few words.
The `_command` row (name, what was typed after it, its line) rides beside the content and on
TURN_START, and no provider sees it. A skill's row is the server's own, built from the name it
checked; a malformed row is dropped, never a reason to refuse the message.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.interface.server.app.user_message import _command
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.wire import wire_message


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _run(client, frame):
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(frame)
        events = []
        while True:
            evt = ws.receive_json()
            events.append(evt)
            if evt["type"] in {"turn_done", "input_rejected"}:
                return events


def _client(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([AssistantTurn(text="done")]))
    return TestClient(create_app(manager))


def test_a_command_rides_beside_the_expanded_text_and_no_provider_sees_it(tmp_path):
    client = _client(tmp_path)
    template = "Write or update AGENTS.md for this repository.\n\nWhat the person asked for:\nkeep it short"
    events = _run(
        client,
        {
            "type": "user_message",
            "text": template,
            "command": {"name": "init", "args": "keep it short", "description": "write a short AGENTS.md", "junk": 1},
        },
    )
    start = next(e for e in events if e["type"] == "turn_start")
    row = {"kind": "prompt", "name": "init", "args": "keep it short", "description": "write a short AGENTS.md"}
    assert start["data"]["command"] == row
    user = next(m for m in client.get("/v1/sessions/s1/messages").json()["messages"] if m["role"] == "user")
    assert user["_command"] == row
    assert user["content"] == template  # the model reads the template, unchanged
    assert "_command" not in wire_message(user)


def test_a_malformed_command_is_dropped_and_the_message_still_goes(tmp_path):
    assert _command({"args": "x"}) is None
    assert _command("init") is None
    assert _command({"name": "  "}) is None
    assert _command({"name": "review", "args": "x" * 5000})["args"] == "x" * 4000
    client = _client(tmp_path)
    events = _run(client, {"type": "user_message", "text": "hello", "command": ["not", "a", "row"]})
    assert "command" not in next(e for e in events if e["type"] == "turn_start")["data"]


def test_a_skill_row_is_built_from_the_name_the_server_checked(tmp_path):
    client = _client(tmp_path)
    client.post("/v1/skills", json={"name": "greet", "description": "says hello", "instructions": "Say hello."})
    events = _run(
        client,
        {
            "type": "user_message",
            "text": "the team",
            "skill": "greet",
            "command": {"name": "something-else", "description": "Says hello warmly"},
        },
    )
    start = next(e for e in events if e["type"] == "turn_start")
    assert start["data"]["command"] == {"kind": "skill", "name": "greet", "args": "the team", "description": "Says hello warmly"}
