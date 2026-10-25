"""The server a surface talks to: its REST routes, the session WebSocket and the gates in front of
both (origin, token, size and rate), as they behave for a client.

Turns are scripted: `ScriptedProvider` answers each model call with the next queued turn, so a test
says exactly what the model "did" and reads back what the client saw.
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness.interface.server import SessionManager, create_app
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.sessions import SessionRecord


class ScriptedProvider(ProviderClient):
    """Answers each model call with the next of `turns`; streaming is the base class's."""

    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool(name, args, call_id="call_1"):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


def _client(tmp_path, turns):
    return TestClient(create_app(SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))))


def _app(manager) -> TestClient:
    return TestClient(create_app(manager))


def _store(manager, session_id, workspace, *, content=None, **fields):
    manager.session_store.save(SessionRecord(
        session_id=session_id, workspace=str(workspace), model=fields.pop("model", "gpt-5.5"), mode="interactive",
        messages=[{"role": "user", "content": content or session_id}], **fields,
    ))


def _drain(ws, on_permission=None):
    """The event types up to `turn_done`, answering a permission request with `on_permission`."""
    seen = []
    while True:
        event = ws.receive_json()
        seen.append(event["type"])
        if event["type"] == "permission_required" and on_permission:
            ws.send_json({"type": "approval", "decision": on_permission})
        if event["type"] == "turn_done":
            return seen


def _sessions(client) -> dict:
    return {row["session_id"]: row for row in client.get("/v1/sessions").json()["sessions"]}


# -- REST ----------------------------------------------------------------------------------------


def test_the_chat_completions_route_answers_in_the_openai_shape(tmp_path):
    reply = _client(tmp_path, [_text("hello world")]).post("/v1/chat/completions", json={"model": "gpt-5.5", "messages": [{"role": "user", "content": "hi"}]})
    assert reply.status_code == 200
    body = reply.json()
    assert body["object"] == "chat.completion"
    assert (body["choices"][0]["message"]["content"], body["choices"][0]["finish_reason"]) == ("hello world", "stop")


def test_agents_and_memory_rest(tmp_path):
    client = _client(tmp_path, [])
    # One agent: there is no list of them to pick from, and no persona pages.
    assert client.get("/v1/agents").status_code == 404
    assert client.get("/v1/personas").status_code == 404
    assert "skills" in client.get("/v1/skills").json()  # served even with no skill installed
    # Memory is files now: the user's directory lists over HTTP.
    assert client.get("/v1/memory/auto", params={"scope": "user"}).json()["files"] == []


def test_subagent_agent_definitions_rest(tmp_path):
    """`GET /v1/subagents` — the `task` agent definitions.
    `/v1/agents` already serves the session-surface picker, so the task-side list has
    its own path; the GUI's @agent picker and the task card read this."""
    agents = {a["name"]: a for a in _client(tmp_path, []).get("/v1/subagents").json()["agents"]}
    assert {"general", "explore"} <= set(agents)
    general = agents["general"]
    assert general["description"].startswith("General-purpose agent")
    assert (general["mode"], general["native"], general["hidden"]) == ("subagent", True, False)
    # The base fields every consumer already reads, plus the Settings ▸ Subagents
    # additions (source/file/overrides, the editable raw fields, the tool badge).
    base = {"name", "description", "mode", "model", "color", "steps", "hidden", "native", "default"}
    assert set(agents["explore"]) == base | {
        "source", "file", "overrides", "prompt", "tools", "inject_agents", "tool_count",
        # External CLI agents carry the provider their `model` belongs to ("" otherwise).
        "provider",
        # The Settings ▸ Subagents enable switch (row stays listed while off).
        "disabled",
    }
    assert (agents["explore"]["source"], agents["explore"]["file"], agents["explore"]["overrides"]) == ("builtin", None, False)


def test_a_retired_persona_id_reads_as_the_one_agent(tmp_path):
    """A session stored under the retired `code` persona keeps its sidebar home: both the
    list and the detail views resolve the id at the read boundary (session row untouched),
    which is what lets the GUI show it with no migration."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _store(manager, "legacy-code", tmp_path, content="old repo session", model="m", title="Legacy", agent="code")
    client = _app(manager)
    assert _sessions(client)["legacy-code"]["agent"] == "reeve"
    assert client.get("/v1/sessions/legacy-code").json()["agent"] == "reeve"
    # The stored row is not rewritten — the mapping is a read-time alias only.
    assert manager.session_store.load("legacy-code").agent == "code"


def test_session_children_rest(tmp_path):
    """`GET /v1/sessions/{id}/children` (P3).

    Direct `kind="subagent"` children only, as `{session_id, title, agent, status}`, with
    the status read from the manager's running set — the sidebar nests the on-path child
    and a reloaded window seeds its registry from this. The roots listing still hides it,
    and an empty query returns an empty list rather than a 404.
    """
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _store(manager, "parent-rest", tmp_path, content="delegate", model="m", title="Parent", agent="reeve")
    child = manager.create_child_session("parent-rest", "Investigate the cache", "explore", "m")
    client = _app(manager)

    assert client.get("/v1/sessions/parent-rest/children").json() == {
        "children": [{"session_id": child, "title": "Investigate the cache (@explore subagent)", "agent": "explore", "status": "idle"}]
    }
    manager.mark_running(child)
    assert client.get("/v1/sessions/parent-rest/children").json()["children"][0]["status"] == "busy"
    manager.mark_idle(child)
    assert list(_sessions(client)) == ["parent-rest"]
    assert client.get("/v1/sessions/unknown/children").json() == {"children": []}


def test_capabilities_rest(tmp_path):
    """`GET /v1/capabilities` (P6): the
    client-visible feature flags. Background spawns ship unconditionally here, so the
    answer is the constant the GUI (and any future client) can gate on."""
    assert _client(tmp_path, []).get("/v1/capabilities").json() == {"background_subagents": True}


def test_session_promote_task_rest(tmp_path):
    """`POST /v1/sessions/{id}/tasks/promote` (P6)
    `POST /experimental/session/:id/background`): an ordinary no-op answers
    `{ok: false, error}` with HTTP 200 — the GUI treats a just-finished child's race as
    normal, not as a failure."""
    client = _client(tmp_path, [])
    assert client.post("/v1/sessions/ghost/tasks/promote").json() == {"ok": False, "error": "no running foreground task to promote"}


def test_a_connector_tool_is_switched_off_through_the_api_and_an_unknown_sessions_audit_is_empty(tmp_path):
    client = _client(tmp_path, [])

    def github_tools():
        listed = {c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]}
        return {tool["name"]: tool for tool in listed["github"]["tools"]}

    # Named from the listing, so the test does not rot with the catalog.
    tool = next(iter(github_tools()))
    assert client.patch("/v1/connectors/github/tools", json={"enabled": {tool: False}}).json()["ok"] is True
    assert github_tools()[tool]["enabled"] is False
    assert client.get("/v1/audit", params={"session_id": "none"}).json()["events"] == []


# -- files a session made ------------------------------------------------------------------------


def _read(client, path):
    return client.get("/v1/sessions/unknown/artifacts/read", params={"path": path}).json()


def test_previewable_files_are_listed_and_read_and_hidden_or_vendored_ones_are_not(tmp_path):
    files = {"plan.md": "# Plan\n\nSteps", "mock.html": "<h1>Mock</h1>", ".draft.md": "not shown", "node_modules/pkg.md": "a dependency's"}
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    client = _client(tmp_path, [])

    listed = {a["path"]: a["kind"] for a in client.get("/v1/sessions/unknown/artifacts").json()["artifacts"]}
    assert (listed["plan.md"], listed["mock.html"]) == ("markdown", "html")
    assert ".draft.md" not in listed and "node_modules/pkg.md" not in listed
    markdown, page = _read(client, "plan.md"), _read(client, "mock.html")
    assert (markdown["ok"], markdown["kind"]) == (True, "markdown") and markdown["content"].startswith("# Plan")
    assert (page["ok"], page["kind"]) == (True, "html") and "<h1>Mock</h1>" in page["content"]


def test_a_folder_reads_as_its_listing_and_a_missing_path_says_it_moved(tmp_path):
    """A linked folder (a skill's package) used to be a dead "not found" (owner, 2026-07-27)."""
    package = tmp_path / "directory-statistics"
    (package / "examples").mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    (package / "stats.py").write_text("print(1)", encoding="utf-8")
    client = _client(tmp_path, [])

    folder = _read(client, "directory-statistics")
    assert folder["ok"] is True and folder["kind"] == "folder"
    entries = folder["entries"]
    assert [e["name"] for e in entries] == ["examples", "SKILL.md", "stats.py"]  # folders first, then by name
    assert entries[0]["dir"] is True and entries[2]["size"] > 0
    missing = _read(client, "nope.md")
    assert missing["ok"] is False and "moved or deleted" in missing["error"]


def test_a_path_out_of_the_workspace_is_refused(tmp_path):
    escaped = _read(_client(tmp_path, []), "../outside.md")
    assert escaped["ok"] is False and "escapes" in escaped["error"]


# -- the sessions list ---------------------------------------------------------------------------


def test_scheduled_tasks_and_their_runs_are_kept_out_of_the_sessions_list(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _store(manager, "normal", tmp_path, content="normal task", title="Normal task", agent="reeve")
    _store(manager, "__run__daily-news-1", tmp_path, content="scheduled run", title="Daily news briefing", agent="reeve")
    _store(manager, "__task__daily-news", tmp_path, content="scheduled task", title="Daily news briefing", agent="reeve")
    assert set(_sessions(_app(manager))) == {"normal"}


def test_a_session_is_renamed_with_its_title_trimmed_and_deleted_with_its_messages(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _store(manager, "rename-me", tmp_path, content="original", title="Original title", agent="reeve")
    client = _app(manager)
    assert client.patch("/v1/sessions/rename-me", json={"title": "  Better title  "}).json()["ok"] is True
    assert _sessions(client)["rename-me"]["title"] == "Better title"
    assert client.delete("/v1/sessions/rename-me").json()["ok"] is True
    assert "rename-me" not in _sessions(client)
    assert client.get("/v1/sessions/rename-me/messages").json()["messages"] == []


def test_a_pinned_session_comes_first_and_pins_and_archives_can_be_undone(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    for session_id in ("older", "newer"):
        _store(manager, session_id, tmp_path, agent="reeve")
    client = _app(manager)

    def patch(session_id, **change):
        return client.patch(f"/v1/sessions/{session_id}", json=change).json()["ok"]

    assert patch("older", pinned=True)
    first = client.get("/v1/sessions").json()["sessions"][0]
    assert first["session_id"] == "older" and first["pinned"] is True
    assert patch("newer", archived=True) and _sessions(client)["newer"]["archived"] is True
    assert patch("older", pinned=False) and patch("newer", archived=False)
    rows = _sessions(client)
    assert rows["older"]["pinned"] is False and rows["newer"]["archived"] is False


def test_a_finished_conversation_is_listed_with_its_messages(tmp_path):
    client = _client(tmp_path, [_text("first answer")])
    with client.websocket_connect("/ws/session/keep") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "remember this"})
        _drain(ws)
    assert _sessions(client)["keep"]["messages"] > 0


def test_session_messages_prefers_the_live_engine(tmp_path):
    """Opening a RUNNING session (e.g. a scheduled automation's first turn) must show the live
    conversation: the persisted record may not exist yet mid-turn — reading only the store gave
    a blank transcript on first open (owner report, 2026-07-04)."""
    client = _client(tmp_path, [_text("ok")])
    engine = client.app.state.manager.get_engine("__run__live", agent="chat")
    engine.messages.append({"role": "user", "content": "hi from a running automation"})
    messages = client.get("/v1/sessions/__run__live/messages").json()["messages"]
    assert any(m.get("content") == "hi from a running automation" for m in messages)


# -- the session WebSocket -----------------------------------------------------------------------


def test_a_turn_over_the_socket_streams_the_answer_and_ends(tmp_path):
    with _client(tmp_path, [_text("done thinking")]).websocket_connect("/ws/session/s1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello"})
        seen = _drain(ws)
    assert "assistant_message" in seen and "turn_end" in seen


def test_ws_user_message_carries_agent_mentions_to_the_turn(tmp_path):
    """P4: the composer's `mentions` field is validated against the mentionable agents,
    the model-facing text gets a synthetic instruction appended, the transcript
    keeps the literal line behind `_display`, and the engine's `turn_mentions` marks the
    turn for the `task` ask-skip. An unknown name is a visible rejection, no turn runs."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("mapped the cache")]))
    with _app(manager).websocket_connect("/ws/session/mention") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "@explore map the cache", "mentions": ["explore"]})
        assert "turn_done" in _drain(ws)
        ws.send_json({"type": "user_message", "text": "@ghost go", "mentions": ["ghost"]})
        rejected = ws.receive_json()
        assert rejected["type"] == "input_rejected" and "Unknown agent type: ghost" in rejected["data"]["error"]

    engine = manager._engines["mention"]
    assert engine.turn_mentions == ["explore"]
    user = next(m for m in engine.messages if m.get("role") == "user")
    assert user["_display"] == "@explore map the cache"
    assert user["content"].startswith("@explore map the cache")
    assert "Use the above message and context to generate a prompt and call the task tool with subagent: explore" in user["content"]


def test_a_too_long_message_or_too_many_attachments_is_refused_and_the_socket_stays(tmp_path):
    from harness.interface.server.app import limits
    from harness.llm.attachments import MAX_ATTACHMENTS

    # The socket's cap is the one the attachment builder enforces.
    assert limits.MAX_ATTACHMENTS == MAX_ATTACHMENTS
    with _client(tmp_path, [_text("should not run")]).websocket_connect("/ws/session/big") as ws:
        assert ws.receive_json()["type"] == "ready"
        for message, said in (
            ({"text": "x" * (limits.MAX_MESSAGE_CHARS + 1)}, "too long"),
            ({"text": "hi", "attachments": ["a"] * (limits.MAX_ATTACHMENTS + 1)}, "attachment"),
        ):
            ws.send_json({"type": "user_message", **message})
            refused = ws.receive_json()
            assert refused["type"] == "input_rejected" and said in refused["data"]["error"].lower()
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_done" in _drain(ws)


def test_each_malformed_frame_is_refused_and_the_socket_keeps_working(tmp_path):
    malformed = [
        [],
        {"type": "user_message", "text": ["not", "text"]},
        {"type": "user_message", "text": "x", "attachments": {}},
        {"type": "user_message", "text": "x", "attachments": [{"kind": "image", "data_url": "https://example.com/x"}]},
        {"type": "set_model", "model": {"unexpected": True}},
        {"type": "unknown"},
    ]
    with _client(tmp_path, [_text("normal")]).websocket_connect("/ws/session/malformed") as ws:
        assert ws.receive_json()["type"] == "ready"
        for frame in malformed:
            ws.send_json(frame)
            assert ws.receive_json()["type"] == "input_rejected"
        ws.send_json({"type": "user_message", "text": "still works"})
        assert "turn_done" in _drain(ws)


def test_a_flood_of_frames_closes_the_socket(tmp_path):
    from harness.interface.server.app import limits

    with pytest.raises(WebSocketDisconnect):
        with _client(tmp_path, []).websocket_connect("/ws/session/rate") as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(limits.RATE_LIMIT_COUNT + 1):
                ws.send_json({"type": "unknown"})
                assert ws.receive_json()["type"] == "input_rejected"
            ws.receive_json()


def test_a_second_message_while_a_turn_runs_is_refused_not_run_beside_it(tmp_path):
    class Slow(ProviderClient):
        def __init__(self):
            self._lock, self.active, self.most = threading.Lock(), 0, 0

        def complete(self, *, model, messages, tools=None, **settings):
            with self._lock:
                self.active += 1
                self.most = max(self.most, self.active)
            try:
                time.sleep(0.08)
                return _text("done")
            finally:
                with self._lock:
                    self.active -= 1

        def capabilities(self, model):
            return ModelCapabilities()

    provider = Slow()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    with _app(manager).websocket_connect("/ws/session/serialized") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "first"})
        ws.send_json({"type": "user_message", "text": "second"})
        seen = []
        while "turn_done" not in seen:
            seen.append(ws.receive_json()["type"])
    assert "input_rejected" in seen and provider.most == 1
    assert [m["content"] for m in manager._engines["serialized"].messages if m.get("role") == "user"] == ["first"]


def test_ws_steer_joins_the_running_turn_instead_of_bouncing(tmp_path):
    """Steering: a sentence typed mid-turn goes into the LIVE turn at its next
    step, and `steering_queued` is the ack the surface renders — the old refusal is
    only for a message that did not ask to steer."""
    gate = threading.Event()

    class GatedProvider(ProviderClient):
        def __init__(self):
            self.calls = []

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                gate.wait(5)  # hold the turn open until the steer is queued
            return _text("done")

        def capabilities(self, model):
            return ModelCapabilities()

    provider = GatedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    with _app(manager).websocket_connect("/ws/session/steered") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "first"})
        deadline = time.time() + 5
        while not provider.calls and time.time() < deadline:
            time.sleep(0.01)
        assert provider.calls, "the first turn never reached the provider"
        ws.send_json({"type": "user_message", "text": "also do this", "steer": True})
        types: list[str] = []
        while "steering_queued" not in types:
            types.append(ws.receive_json()["type"])
        gate.set()
        while "turn_done" not in types:
            types.append(ws.receive_json()["type"])

    assert "input_rejected" not in types
    # The same turn read it at its next step: the second model call ends on the
    # steering message and the history carries it exactly once.
    assert provider.calls[1][-1]["content"] == "also do this"
    user_messages = [m for m in manager._engines["steered"].messages if m.get("role") == "user"]
    assert [m["content"] for m in user_messages] == ["first", "also do this"]
    # Marked for the transcript; the provider feed above saw the plain message.
    assert user_messages[1].get("_steer") is True


def test_ws_a_steer_does_not_wait_behind_a_long_command(tmp_path):
    """A message typed into a turn that is waiting on a slow command is read within the grace,
    not when the command ends: the command goes on in the background and the model is told why
    its wait ended (2026-10-24: 48 steers in the store came right after a tool of 30 s or more)."""
    from harness.tools import shell_wait

    # PowerShell (the Windows default) calls a quoted path with `&`.
    command = ("& " if sys.platform == "win32" else "") + f'"{sys.executable}" -c "import time; time.sleep(60)"'

    class SlowCommandProvider(ProviderClient):
        def __init__(self):
            self.calls = []

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return _tool("run_shell", {"command": command})
            return _text("done")

        def capabilities(self, model):
            return ModelCapabilities()

    old_grace = shell_wait.RELEASE_GRACE_SECONDS
    shell_wait.RELEASE_GRACE_SECONDS = 0.5
    try:
        provider = SlowCommandProvider()
        manager = SessionManager(workspace=tmp_path, provider=provider)
        with _app(manager).websocket_connect("/ws/session/slowcmd") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "set_mode", "mode": "danger-full-access"})
            ws.send_json({"type": "user_message", "text": "run it"})
            deadline = time.monotonic() + 10
            while len(provider.calls) < 1 and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(1.0)  # the command is in its wait
            types: list[str] = []
            sent = time.monotonic()
            ws.send_json({"type": "user_message", "text": "stop waiting, answer me", "steer": True})
            while "turn_done" not in types:
                types.append(ws.receive_json()["type"])
            took = time.monotonic() - sent
        assert took < 15, took
        # The model read the command's result, then the person's message, in the same turn.
        second = provider.calls[1]
        assert second[-1]["content"] == "stop waiting, answer me"
        result = next(m for m in reversed(second) if m.get("role") == "tool")
        assert "the user sent a message" in str(result.get("content"))
        assert not manager._engines["slowcmd"].person_waiting.is_set()
    finally:
        shell_wait.RELEASE_GRACE_SECONDS = old_grace
        for session in list(manager._engines.values()):
            executor = getattr(session, "executor", None)
            for job in list(getattr(executor, "_bg_tasks", {}).values()):
                job.kill()


def test_ws_ready_reports_a_turn_already_in_flight(tmp_path):
    """A page reload mid-turn must learn the session is busy from `ready`: otherwise
    the composer draws Send instead of Stop/Steer and the next message bounces off
    "already running a turn"."""
    gate = threading.Event()

    class GatedProvider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            gate.wait(5)
            return _text("done")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(workspace=tmp_path, provider=GatedProvider())
    client = _app(manager)
    with client.websocket_connect("/ws/session/busyready") as ws:
        assert ws.receive_json()["data"]["running"] is False
        ws.send_json({"type": "user_message", "text": "go"})
        deadline = time.time() + 5
        while not manager.is_running("busyready") and time.time() < deadline:
            time.sleep(0.01)
        assert manager.is_running("busyready")
        # The reload case: a second view of the same session gets the busy truth.
        with client.websocket_connect("/ws/session/busyready") as ws2:
            assert ws2.receive_json()["data"]["running"] is True
        gate.set()
        _drain(ws)


def test_ws_steering_refuses_anything_but_plain_text(tmp_path):
    """Attachments, skills and @mentions frame a NEW turn (or carry turn-scoped
    bypasses), so they cannot ride a splice — the refusal is visible, never silent."""
    with _client(tmp_path, [_text("ok")]).websocket_connect("/ws/session/steerplain") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "and check this file", "steer": True, "attachments": [{"kind": "text", "name": "a.txt", "text": "hi"}]})
        rejected = ws.receive_json()
        assert rejected["type"] == "input_rejected" and "plain text" in rejected["data"]["error"]
        ws.send_json({"type": "user_message", "text": "still works"})
        assert "turn_done" in _drain(ws)


def test_ws_set_mode_and_plan_log_and_broadcast_the_change(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("ok")]))
    with _app(manager).websocket_connect("/ws/session/modes") as ws:
        ready = ws.receive_json()
        assert ready["data"]["mode"] == "workspace-write" and ready["data"]["plan"] is False

        ws.send_json({"type": "set_mode", "mode": "read-only"})
        changed = ws.receive_json()
        assert changed["type"] == "mode_changed" and changed["data"]["mode"] == "read-only"
        # Legacy spellings still validate through the Mode migration map.
        ws.send_json({"type": "set_mode", "mode": "auto"})
        assert ws.receive_json()["data"]["mode"] == "danger-full-access"
        # Re-selecting the current mode is not a change: no frame, no log row.
        ws.send_json({"type": "set_mode", "mode": "danger-full-access"})

        # The plan overlay is a SEPARATE axis with its own frame and event.
        ws.send_json({"type": "set_plan", "plan": True})
        plan_changed = ws.receive_json()
        assert plan_changed["type"] == "plan_changed" and plan_changed["data"]["plan"] is True
        # A repeated toggle is not a change either.
        ws.send_json({"type": "set_plan", "plan": True})

        ws.send_json({"type": "user_message", "text": "hi"})
        types = _drain(ws)
        assert "mode_changed" not in types and "plan_changed" not in types

    log = manager._engines["modes"].log.read()
    assert [(e["from"], e["to"], e["reason"]) for e in log if e["type"] == "mode/changed"] == [
        ("workspace-write", "read-only", "user"),
        ("read-only", "danger-full-access", "user"),
    ]
    assert [(e["active"], e["reason"]) for e in log if e["type"] == "plan/mode"] == [(True, "user")]


def test_full_access_writes_without_asking(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    manager = SessionManager(workspace=None, data_dir=tmp_path, provider=ScriptedProvider([_tool("write_file", {"path": "a.py", "content": "x"}), _text("done")]))
    with _app(manager).websocket_connect(f"/ws/session/sm?workspace={quote(str(project))}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "set_mode", "mode": "auto"})
        ws.send_json({"type": "user_message", "text": "write a.py"})
        assert "permission_required" not in _drain(ws)  # no approval is answered: an ask would hang
    assert (project / "a.py").read_text() == "x"


# -- the model and its thinking level ------------------------------------------------------------


def test_ws_set_thinking_marks_and_persists(tmp_path):
    """A composer thinking-level change: applied to the live engine, persisted on the
    session, and echoed as a marker so other live views update their header."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))

    def next_thinking(ws):
        for _ in range(10):
            event = ws.receive_json()
            if event["type"] == "thinking_changed":
                return event
        raise AssertionError("no thinking_changed")

    with _app(manager).websocket_connect("/ws/session/thk") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["data"].get("thinking") in (None, "")
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_done" in _drain(ws)
        ws.send_json({"type": "set_thinking", "thinking": "high"})
        assert next_thinking(ws)["data"]["thinking"] == "high"
        # Clearing the override is the picker's "Default".
        ws.send_json({"type": "set_thinking", "thinking": ""})
        assert next_thinking(ws)["data"]["thinking"] == ""
    record = manager.session_store.load("thk")
    assert record is not None and record.thinking is None


def test_ws_a_model_switch_that_drops_the_level_says_so(tmp_path):
    """Owner-hit 2026-10-24: the switch dropped a level the new model does not list, but only
    `model_changed` went out, so the picker kept the old model's level."""
    from harness.llm.providers.catalog import ModelsDevCatalog, set_models_dev

    class _Facts(ModelsDevCatalog):
        def __init__(self, entries, path):
            super().__init__(path)
            self._entries, self._fetched_at, self._tried = dict(entries), float("inf"), True

    three, two = "openai:three-levels", "openai:two-levels"
    set_models_dev(_Facts({
        three: {"reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}]},
        two: {"reasoning_options": [{"type": "effort", "values": ["low", "high"]}]},
    }, tmp_path / "facts.json"))
    try:
        manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
        with _app(manager).websocket_connect("/ws/session/drop") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "set_model", "model": three})
            ws.send_json({"type": "set_thinking", "thinking": "medium"})
            ws.send_json({"type": "user_message", "text": "hello"})
            assert "turn_done" in _drain(ws)
            ws.send_json({"type": "set_model", "model": two})
            # A frame the socket refuses marks the end of what the switch sent, so a missing
            # broadcast fails the test instead of blocking the read.
            ws.send_json({"type": "end-of-switch"})
            seen = []
            while not seen or seen[-1]["type"] != "input_rejected":
                seen.append(ws.receive_json())
            assert [evt["type"] for evt in seen][:2] == ["model_changed", "thinking_changed"]
            assert seen[1]["data"]["thinking"] == ""
        assert manager.session_store.load("drop").thinking is None
    finally:
        set_models_dev(None)


def test_ws_first_message_binds_then_midsession_switch_persists_notice(tmp_path):
    """The first bind on a fresh session leaves no notice (race-proof across reconnects —
    found 2026-07-04), only the `model_changed` fact with no text. Mid-session rebinds are ALLOWED (roadmap item 3,
    2026-07-22, supersedes the 07-04 lock): the switch lands as a persisted model_switch
    notice and a model_changed broadcast, and the next turn runs on the new model."""
    # In the order they are asked for: turn 1, the title written after it, turn 2, then the memory
    # pass (after the second turn) and turn 3, which may take either of the last two slots.
    client = _client(tmp_path, [_text("ok"), _text("Session title"), _text("ok again"), _text("[]"), _text("still ok")])
    with client.websocket_connect("/ws/session/model-per-msg") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "set_model", "model": "zai:glm-5.2"})
        # The first bind marks nothing (no history), but the views hear the fact: a later
        # mode_changed re-applies what they hold, and a stale model reset the picker.
        bound = ws.receive_json()
        assert bound["type"] == "model_changed" and bound["data"] == {"model": "zai:glm-5.2", "text": None}
        ws.send_json({"type": "user_message", "text": "hi", "model": "zai:glm-5.2"})
        assert "model_changed" not in _drain(ws)  # the same model again: nothing to say
        # A message without a model keeps the bound one.
        ws.send_json({"type": "user_message", "text": "again"})
        _drain(ws)
        ws.send_json({"type": "set_model", "model": "kimi:kimi-k2.6"})
        changed = ws.receive_json()
        assert changed["type"] == "model_changed" and changed["data"]["model"] == "kimi:kimi-k2.6"
        assert "Kimi" in changed["data"]["text"]
        ws.send_json({"type": "user_message", "text": "switched now"})
        _drain(ws)
    engine = client.app.state.manager._engines["model-per-msg"]
    assert engine.model == "kimi:kimi-k2.6"
    # The marker is persisted between the turns; the provider never sees it.
    messages = client.get("/v1/sessions/model-per-msg/messages").json()["messages"]
    assert [m["kind"] for m in messages if m["role"] == "notice"] == ["model_switch"]
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())


# -- a failed turn -------------------------------------------------------------------------------


def test_a_failed_turn_leaves_a_notice_and_retry_runs_it_again_without_a_new_message(tmp_path):
    class Flaky(ProviderClient):
        """Down for the first request, fine after it."""

        def __init__(self):
            self.down = True

        def complete(self, *, model, messages, tools=None, **settings):
            if self.down:
                self.down = False
                raise RuntimeError("provider unavailable")
            return _text("recovered")

        def capabilities(self, model):
            return ModelCapabilities()

    client = _app(SessionManager(workspace=tmp_path, provider=Flaky()))

    def messages():
        return client.get("/v1/sessions/flaky/messages").json()["messages"]

    with client.websocket_connect("/ws/session/flaky") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "error" in _drain(ws)
        assert (messages()[-1]["role"], messages()[-1]["kind"]) == ("notice", "error")  # a reload shows it
        ws.send_json({"type": "retry"})
        seen = _drain(ws)
        assert "turn_start" in seen and "assistant_message" in seen
    assert (messages()[-1]["role"], messages()[-1]["content"]) == ("assistant", "recovered")
    assert sum(1 for m in messages() if m["role"] == "user") == 1


# -- who may talk to the server ------------------------------------------------------------------


def test_cross_origin_reads_are_allowed_only_to_the_apps_own_origins(tmp_path):
    client = _client(tmp_path, [])

    def allowed(origin):
        return client.get("/v1/sessions", headers={"Origin": origin}).headers.get("access-control-allow-origin")

    # A web page elsewhere gets no allow header, so the browser blocks the read.
    assert allowed("https://evil.example") is None
    for origin in ("tauri://localhost", "http://localhost:1420", "reeve://app"):  # the webview, the dev build, Electron
        assert allowed(origin) == origin
    assert allowed("reeve://app.evil.example") is None  # only the scheme itself, not a look-alike


@pytest.mark.parametrize(("origin", "opens"), [("https://evil.example", False), ("http://tauri.localhost", True), ("reeve://app", True)])
def test_the_socket_opens_only_for_the_apps_own_origins(tmp_path, origin, opens):
    client = _client(tmp_path, [_text("hi")])
    if opens:
        with client.websocket_connect("/ws/session/x", headers={"Origin": origin}) as ws:
            assert ws.receive_json()["type"] == "ready"
        return
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/session/x", headers={"Origin": origin}) as ws:
            ws.receive_json()
    assert closed.value.code == 1008


def test_with_a_token_set_every_route_and_socket_needs_it_but_health_and_the_mcp_sign_in(tmp_path, monkeypatch):
    from harness.extensions.mcp.config import global_mcp_path

    token = "a" * 64
    monkeypatch.setenv("REEVE_API_TOKEN", token)
    client = _app(SessionManager(workspace=tmp_path, provider=ScriptedProvider([])))
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/v1/sessions").status_code == 401
    assert client.get("/v1/sessions", headers={"X-Reeve-Token": "wrong"}).status_code == 401
    headers = {"X-Reeve-Token": token}
    assert client.get("/v1/health", headers=headers).json()["default_workspace"] == str(tmp_path.resolve())
    assert client.get("/v1/sessions", headers=headers).status_code == 200
    # Without it, nothing is written either.
    assert client.post("/v1/mcp", json={"name": "evil", "config": {"command": "sh", "args": ["-c", "id"]}}).status_code == 401
    assert not global_mcp_path().exists()

    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect("/ws/session/tokenless") as ws:
            ws.receive_json()
    assert denied.value.code == 1008
    with client.websocket_connect("/ws/session/authed", subprotocols=["reeve", token]) as ws:
        assert ws.accepted_subprotocol == "reeve" and ws.receive_json()["type"] == "ready"
    with client.websocket_connect("/ws/events", subprotocols=["reeve", token]) as ws:
        assert ws.accepted_subprotocol == "reeve"

    # The MCP sign-in's redirect callback remains tokenless, then enforces its own state.
    assert client.get("/mcp/oauth/callback").status_code == 400
    # The cloud's callbacks are gone and no longer exempt from the token: they are just paths.
    assert client.get("/auth/callback", params={"code": "x", "state": "bad"}).status_code == 401
    assert client.post("/oauth/callback", data={"app_state": "bad"}).status_code == 401


def test_the_server_sets_a_frame_limit_and_hands_the_app_its_uvicorn_server(tmp_path, monkeypatch):
    from harness.interface.server import run as server_run
    from harness.interface.server import supervision
    from harness.interface.server.app import limits

    seen = {}
    # `POST /v1/shutdown` needs the uvicorn Server instance off the app: the graceful-quit
    # handle is attached here, and a plain object would hide its absence (it did).
    app = SimpleNamespace(state=SimpleNamespace())

    class _Config:
        def __init__(self, app, **kwargs):
            seen.update(app=app, **kwargs)
            self.kwargs = kwargs

    class _Server:
        def __init__(self, config):
            self.config, self.should_exit = config, False

        async def serve(self):
            return  # one orderly pass; supervision is not what this test measures

    monkeypatch.setattr(server_run, "_ensure_ca_bundle", lambda: None)
    monkeypatch.setattr(server_run, "exit_when_orphaned", lambda: None)
    monkeypatch.setattr(server_run, "build_app", lambda *args: app)
    monkeypatch.setattr(supervision, "port_alive", lambda *a, **k: True)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=_Config, Server=_Server))
    server_run.main(["--cwd", str(tmp_path), "--port", "8766"])
    assert seen["app"] is app and seen["ws_max_size"] == limits.MAX_FRAME_BYTES
    assert isinstance(app.state.uvicorn_server, _Server)


def test_a_standalone_server_writes_its_token_to_a_file_only_the_user_can_read(tmp_path, monkeypatch):
    import os

    from harness.interface.server import run as server_run

    monkeypatch.delenv("REEVE_API_TOKEN", raising=False)
    path = server_run._ensure_api_token(9876)
    try:
        assert path == tmp_path / "reeve-state" / "sidecar-9876.token"
        token = path.read_text().strip()
        assert token == os.environ["REEVE_API_TOKEN"] and len(token) == 64
        if os.name != "nt":  # Windows has no mode bits
            assert (path.stat().st_mode & 0o777) == 0o600
    finally:
        path.unlink(missing_ok=True)
        os.environ.pop("REEVE_API_TOKEN", None)


# -- approvals and the audit ---------------------------------------------------------------------


def test_an_approved_write_runs(tmp_path):
    client = _client(tmp_path, [_tool("write_file", {"path": "made.py", "content": "print(1)\n"}), _text("wrote it")])
    with client.websocket_connect("/ws/session/s2") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "create made.py"})
        seen = _drain(ws, on_permission="once")
    assert "permission_required" in seen and "tool_finished" in seen
    assert (tmp_path / "made.py").read_text() == "print(1)\n"


def test_a_turn_waiting_for_approval_is_already_saved(tmp_path):
    """A crash while the turn waits for the person must not lose the conversation."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_tool("write_file", {"path": "x.py", "content": "1\n"}), _text("done")]))
    with _app(manager).websocket_connect("/ws/session/persist1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "make x.py"})
        while ws.receive_json()["type"] != "permission_required":
            pass
        saved = manager.session_store.load("persist1")
        assert saved is not None
        roles = [m.get("role") for m in saved.messages]
        assert "user" in roles and "assistant" in roles  # the message and the call that asks
        ws.send_json({"type": "approval", "decision": "deny"})
        while ws.receive_json()["type"] != "turn_done":
            pass


def test_an_approved_command_leaves_its_approval_and_its_end_in_the_audit(tmp_path):
    client = _client(tmp_path, [_tool("run_shell", {"command": "echo hi | tee hi.txt"}), _text("ran")])
    with client.websocket_connect("/ws/session/shell-audit?agent=reeve") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run it"})
        seen = _drain(ws, on_permission="once")
    assert "permission_required" in seen and "tool_finished" in seen
    stages = {(row["tool"], row["stage"]) for row in client.get("/v1/audit", params={"session_id": "shell-audit"}).json()["events"]}
    assert {("run_shell", "approval_resolved"), ("run_shell", "finished")} <= stages


def test_a_command_always_allowed_is_not_asked_about_again_after_a_restart(tmp_path):
    """The grant belongs to the session, and the session outlives the process: a new manager over
    the same store must not ask again about a command already allowed (the owner met it on the
    0.1.6 walkthrough, 2026-07-22)."""

    def run(expect_prompts):
        manager = SessionManager(workspace=None, provider=ScriptedProvider([_tool("run_shell", {"command": "uname -a | tee uname.txt"}, call_id="c1"), _text("done")]))
        with _app(manager).websocket_connect("/ws/session/grants1?agent=reeve") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user_message", "text": "run it"})
            asked = 0
            while True:
                event = ws.receive_json()
                if event["type"] == "permission_required":
                    asked += 1
                    ws.send_json({"type": "approval", "decision": "always_command"})
                if event["type"] == "turn_done":
                    break
        assert asked == expect_prompts

    run(expect_prompts=1)
    run(expect_prompts=0)  # a new manager, the engine built again from what was saved


def test_permission_endpoints_list_and_reply(tmp_path):
    """`GET /v1/permissions` + `POST /v1/permissions/{id}/reply` (P1 / F4)
    `GET`/`POST /permission`): the list is the documented pending shape, a reply resolves
    the parked future and empties the registry, and an unknown / already-answered /
    malformed request is `{ok: false, error}` rather than a crash."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    # `with` runs the app lifespan, which installs the manager loop the ask parks on.
    with TestClient(create_app(manager)) as client:

        def reply(request_id, **body):
            return client.post(f"/v1/permissions/{request_id}/reply", json=body).json()

        future = manager.request_permission(
            session_id="child-1", parent_id="root-1", permission="run_shell",
            patterns=["rm -rf build"], metadata={"kind": "exec"}, always=["rm -rf build"],
        )
        [listed] = client.get("/v1/permissions").json()["permissions"]
        assert listed == {
            "request_id": listed["request_id"], "session_id": "child-1", "parent_session": "root-1",
            "permission": "run_shell", "patterns": ["rm -rf build"], "metadata": {"kind": "exec"},
        }
        rid = listed["request_id"]
        assert reply(rid, reply="once") == {"ok": True}
        assert future.done() and future.result().reply == "once"
        assert client.get("/v1/permissions").json()["permissions"] == []
        # Already answered: the same id is gone, so it is the unknown-request error.
        again = reply(rid, reply="once")
        assert again["ok"] is False and again["error"]
        unknown = reply("per_ghost", reply="once")
        assert unknown["ok"] is False and "unknown" in unknown["error"]

        # A malformed reply leaves the request parked for a real answer.
        second = manager.request_permission(session_id="session-2", permission="write_file", patterns=["a.py"])
        rid2 = client.get("/v1/permissions").json()["permissions"][0]["request_id"]
        assert reply(rid2, reply="maybe") == {"ok": False, "error": "unknown reply: maybe"}
        assert not second.done()
        assert reply(rid2, reply="reject", feedback="not that file") == {"ok": True}
        outcome = second.result()
        assert outcome.reply == "reject" and outcome.denial_message.endswith("feedback: not that file")


# -- workspaces ----------------------------------------------------------------------------------


def _open(client, path, **body):
    return client.post("/v1/workspaces/open", json={"path": str(path), **body}).json()


def test_an_opened_folder_is_recent_a_missing_one_is_refused_and_create_makes_it(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    client = _client(tmp_path, [])
    assert _open(client, project)["ok"] is True
    assert any(w["path"] == str(project.resolve()) for w in client.get("/v1/workspaces/recent").json()["workspaces"])
    assert _open(client, tmp_path / "nope")["ok"] is False
    fresh = tmp_path / "fresh-project"
    assert _open(client, fresh, create=True)["ok"] is True and fresh.is_dir()


def test_scratch_folders_are_never_recent_projects(tmp_path):
    """They are touched like any workspace (owner, 2026-07-03)."""
    project = tmp_path / "real-project"
    project.mkdir()
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    manager._prefs["scratch_base"] = str(tmp_path / "scratch")
    scratch = manager._provision_scratch("sess-1")
    manager.session_store.touch_workspace(str(project.resolve()))
    manager.session_store.touch_workspace(scratch)
    recent = [w["path"] for w in manager.recent_workspaces()]
    assert str(project.resolve()) in recent and scratch not in recent


def test_deleting_a_session_deletes_its_scratch_folder_and_never_a_project(tmp_path):
    """Owner, 2026-07-03: a conversation's scratch goes with it; a folder the person picked never does."""
    from pathlib import Path

    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    manager._prefs["scratch_base"] = str(tmp_path / "scratch")
    scratch = Path(manager._provision_scratch("sess-scratch"))
    manager.session_store.save(SessionRecord(session_id="sess-scratch", workspace=str(scratch), model="m", mode="interactive"))
    assert manager.delete_session("sess-scratch")["ok"] and not scratch.exists()

    project = tmp_path / "real-project"
    project.mkdir()
    manager.session_store.save(SessionRecord(session_id="sess-proj", workspace=str(project), model="m", mode="interactive"))
    assert manager.delete_session("sess-proj")["ok"] and project.is_dir()


def test_a_socket_opened_on_a_folder_works_there(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    manager = SessionManager(workspace=None, data_dir=tmp_path, provider=ScriptedProvider([_text("hi from proj")]))
    with _app(manager).websocket_connect(f"/ws/session/s?workspace={quote(str(project))}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["data"]["workspace"] == str(project.resolve())
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_end" in _drain(ws)


def test_a_removed_agent_id_opens_on_the_one_agent(tmp_path):
    """Chat is gone (owner, 2026-08-21): a stored session or link naming it opens on the agent."""
    manager = SessionManager(workspace=None, data_dir=tmp_path, provider=ScriptedProvider([_text("hi")]))
    with _app(manager).websocket_connect("/ws/session/chat1?agent=chat") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["data"]["agent"] == "reeve"
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_end" in _drain(ws)


def test_trusting_a_workspace_lets_its_commands_run_and_revoking_asks_again(tmp_path):
    project = tmp_path / "trusted-project"
    (project / ".reeve").mkdir(parents=True)
    (project / ".reeve" / "config.toml").write_text('allowed_commands = ["pytest"]\nauto_allow = ["write_file"]\n')
    manager = SessionManager(workspace=None, data_dir=tmp_path / "data", provider=ScriptedProvider([]))
    client = _app(manager)

    def trust(trusted, path=project):
        return client.post("/v1/workspaces/trust", json={"path": str(path), "trusted": trusted}).json()

    with client.websocket_connect(f"/ws/session/trust?workspace={quote(str(project))}") as ws:
        policy = ws.receive_json()["data"]["command_trust"]
        assert policy["required"] is True and policy["requested_commands"] == ["pytest"]
        engine = manager._engines["trust"]

        def pytest_verdict():
            return engine.permissions.evaluate("run_shell", {"command": "pytest -q"}, None)

        assert not pytest_verdict().allowed and pytest_verdict().needs_user
        # A workspace's auto_allow is ignored, trusted or not.
        assert "write_file" not in engine.permissions.auto_allow_tools
        trusted = trust(True)
        assert trusted["ok"] and trusted["trusted"] and pytest_verdict().allowed
        assert [item["workspace"] for item in client.get("/v1/workspaces/trusted").json()["workspaces"]] == [str(project.resolve())]
        revoked = trust(False)
        assert revoked["ok"] and not revoked["trusted"]
        assert not pytest_verdict().allowed and pytest_verdict().needs_user

    manager.workspace_trust.set_trusted(project, True)
    # Windows will not rename a folder a live process works in, and this session's shell started
    # there: close it first, as `save_temp_as_project` does before moving a temp workspace.
    if getattr(engine, "executor", None) is not None:
        engine.executor.close()
    manager._engines.pop("trust", None)
    project.rename(tmp_path / "moved-project")
    # Trust held for a folder that moved can still be revoked by its old path.
    assert trust(False)["ok"] and manager.trusted_workspaces() == []


def test_pick_native_folder_paths(tmp_path, monkeypatch):
    """The sidecar-side folder picker (for browser GUIs): picked path round-trips; cancel and
    missing-picker degrade to ok:False without raising."""
    import subprocess

    client = _client(tmp_path, [])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="/tmp/picked\n", stderr=""))
    assert client.post("/v1/workspaces/pick").json() == {"ok": True, "path": "/tmp/picked"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="User canceled."))
    assert client.post("/v1/workspaces/pick").json()["ok"] is False

    def no_picker(*a, **k):
        raise OSError("no zenity")

    monkeypatch.setattr(subprocess, "run", no_picker)
    out = client.app.state.manager.pick_native_folder()
    assert out["ok"] is False and "picker" in out["error"]


# -- providers -----------------------------------------------------------------------------------


def test_provider_set_and_remove_roundtrip(tmp_path):
    """Settings ▸ Models "Remove key": DELETE /v1/providers/{name} forgets the stored
    profile so the provider reads unconfigured again; unknown names are a clean error.
    """
    client = _client(tmp_path, [])

    def zai():
        return {p["name"]: p for p in client.get("/v1/providers").json()}["zai"]

    assert client.post("/v1/providers", json={"name": "zai", "fields": {"api_key": "zk-test"}}).json()["ok"]
    assert zai()["configured"] and zai()["key_set_at"]
    assert client.delete("/v1/providers/zai").json()["ok"]
    assert not zai()["configured"] and not zai()["key_set_at"]
    assert not client.delete("/v1/providers/nope").json()["ok"]


def test_a_providers_endpoint_is_saved_shown_for_the_form_and_cleared_by_a_blank_save(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))

    def ollama_values():
        return {p["name"]: p for p in manager.get_providers()}["ollama"]["values"]

    assert manager.set_provider("ollama", {"base_url": "http://127.0.0.1:9999"})["ok"]
    assert ollama_values()["base_url"] == "http://127.0.0.1:9999"
    manager.set_provider("ollama", {"base_url": ""})
    assert "base_url" not in ollama_values()


def test_mcp_connect_route_flags_authorizing_immediately(tmp_path, monkeypatch):
    """Owner-hit 2026-08-21: the Test button looked dead — the connect ran as a
    background task, and the GUI's first refresh landed before the task set
    `authorizing`, so the fast poll never armed. The route must flag it
    synchronously (and only for known servers, so nothing wedges)."""
    import asyncio

    manager = SessionManager(data_dir=tmp_path / "data")
    monkeypatch.setattr("harness.interface.server.manager.mcp_servers.read_global", lambda: {"sales-db": {"command": "x"}})
    manager.begin_mcp_connect("sales-db")
    assert "sales-db" in manager._mcp_authorizing
    manager.begin_mcp_connect("nope")
    assert "nope" not in manager._mcp_authorizing
    # An unmatched name clears the flag instead of wedging "Testing…" forever.
    monkeypatch.setattr("harness.interface.server.manager.mcp_servers.load_mcp_servers", lambda *a, **k: [])
    result = asyncio.run(manager.connect_mcp("sales-db"))
    assert not result["ok"] and "sales-db" not in manager._mcp_authorizing


# -- graceful shutdown ---------------------------------------------------------------------------


def test_shutdown_endpoint_asks_uvicorn_to_stop(tmp_path):
    app = create_app(SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("x")])))
    server = SimpleNamespace(should_exit=False)
    app.state.uvicorn_server = server
    assert TestClient(app).post("/v1/shutdown").json() == {"ok": True}
    assert server.should_exit is True


def test_shutdown_without_a_server_handle_says_so(tmp_path):
    """A bare `create_app` (tests, an embedded run) has no uvicorn Server to stop; saying
    so beats pretending the app is closing."""
    body = _client(tmp_path, [_text("x")]).post("/v1/shutdown").json()
    assert body["ok"] is False and "no server handle" in body["error"]


def test_a_turn_is_refused_once_we_are_shutting_down(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("late")]))
    with _app(manager).websocket_connect("/ws/session/bye") as ws:
        assert ws.receive_json()["type"] == "ready"
        manager.shutting_down = True
        ws.send_json({"type": "user_message", "text": "anyone there?"})
        rejected = ws.receive_json()
        assert rejected["type"] == "input_rejected" and "closing" in rejected["data"]["error"]
        assert manager.is_running("bye") is False, "the claim was released"


def test_drain_waits_for_running_turns_but_not_forever(tmp_path):
    import asyncio

    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("x")]))

    async def go():
        manager.try_mark_running("s1")
        manager.try_mark_running("s2")

        async def finish_later():
            await asyncio.sleep(0.15)
            manager.mark_idle("s1")

        asyncio.ensure_future(finish_later())
        started = time.monotonic()
        left = await manager.drain_running(timeout=1.0)
        elapsed = time.monotonic() - started
        assert elapsed >= 0.1, "it waited for the finishing turn"
        assert left == 1, "the one that never finished is reported"
        # Bounded: the deadline plus at most one poll interval.
        assert elapsed < 1.2
        # A zero window is honest: it reports what is still running and returns.
        assert await manager.drain_running(timeout=0) == 1

    asyncio.run(go())


# -- reopening a session -------------------------------------------------------------------------


def test_a_reopened_session_asks_which_calls_it_can_still_undo(tmp_path):
    """The transcript does not record undoability, so the Review panel of a reopened session
    showed every edit as not undoable while the turn's receipt still offered Undo. The
    route answers from the session's undo store."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_tool("write_file", {"path": "x.py", "content": "1\n"}), _text("done")]))
    client = _app(manager)
    with client.websocket_connect("/ws/session/undoable1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "make x.py"})
        call_id = None
        while True:
            event = ws.receive_json()
            if event["type"] == "permission_required":
                ws.send_json({"type": "approval", "decision": "once"})
            if event["type"] == "tool_finished":
                call_id = event["data"].get("call_id")
            if event["type"] == "turn_done":
                break
    assert call_id
    assert client.get("/v1/sessions/undoable1/undoable").json() == {"call_ids": [call_id]}
    assert client.post("/v1/sessions/undoable1/undo", json={"call_id": call_id}).json()["ok"]
    assert client.get("/v1/sessions/undoable1/undoable").json() == {"call_ids": []}
    assert client.get("/v1/sessions/nobody/undoable").json() == {"call_ids": []}


def test_default_mode_starts_new_conversations_and_leaves_existing_ones(tmp_path):
    """Settings ▸ General's default mode (owner, 2026-09-27: Full access was picked by hand
    in 140 sessions): a new conversation opens in it, one already started keeps its own
    mode, and the choice survives a restart. An unknown value changes nothing."""
    client = _app(SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("ok")])))
    with client.websocket_connect("/ws/session/before") as ws:
        assert ws.receive_json()["data"]["mode"] == "workspace-write"
        ws.send_json({"type": "user_message", "text": "hi"})
        _drain(ws)
    assert client.post("/v1/settings/default-mode", json={"mode": "everything"}).json()["ok"] is False
    saved = client.post("/v1/settings/default-mode", json={"mode": "danger-full-access"}).json()
    assert saved["ok"] is True and saved["default_mode"] == "danger-full-access"

    client = _app(SessionManager(workspace=tmp_path, provider=ScriptedProvider([])))
    assert client.get("/v1/settings").json()["default_mode"] == "danger-full-access"
    with client.websocket_connect("/ws/session/after") as ws:
        assert ws.receive_json()["data"]["mode"] == "danger-full-access"
    with client.websocket_connect("/ws/session/before") as ws:
        assert ws.receive_json()["data"]["mode"] == "workspace-write"
