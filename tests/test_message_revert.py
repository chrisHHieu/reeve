"""Message-level revert — `/undo` and `/redo` over the canonical transcript.

What must hold: the marker is session state (nothing is deleted), the outbound view is
`messages[:boundary]` plus the post-undo tail, the transcript itself is untouched, the
marker survives a reload and a per-turn save, redo walks forward or clears, a fork does
not inherit the marker, and a compaction whose summarized span contains hidden messages
is suspended rather than leaking them as a summary.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.session.revert import compaction_fits, hidden_count, marker_boundary, revert_view, visible_user_indexes
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools import ToolRegistry


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=Provider())


def _record(mgr: SessionManager, sid: str, messages: list[dict]) -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace or "."),
        model="gpt-5.5",
        mode="interactive",
        messages=messages,
    )
    mgr.session_store.save(record)
    return record


CONVERSATION = [
    {"role": "system", "content": "be helpful"},
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "first answer"},
    {"role": "user", "content": "second question"},
    {"role": "assistant", "content": "second answer"},
    {"role": "user", "content": "third question"},
    {"role": "assistant", "content": "third answer"},
]


def test_an_in_place_tail_update_is_persisted(tmp_path, monkeypatch):
    """Same message count, different last message: the append-only fast path must not
    swallow it. An external child's live progress text is rewritten that way (and the
    final answer replaces it), so the store rewrites the file instead."""
    mgr = _manager(tmp_path, monkeypatch)
    record = _record(
        mgr,
        "child-1",
        [
            {"role": "user", "content": "delegate this"},
            {"role": "assistant", "content": "working…"},
        ],
    )

    record.messages[-1] = {"role": "assistant", "content": "the final answer"}
    mgr.session_store.save(record)
    assert mgr.session_store.load("child-1").messages[-1]["content"] == "the final answer"

    # An unchanged record still costs nothing: the tail comparison reads the last line
    # and skips the rewrite.
    before = mgr.session_store._file("child-1").read_text(encoding="utf-8")
    mgr.session_store.save(record)
    assert mgr.session_store._file("child-1").read_text(encoding="utf-8") == before


def _engine(tmp_path, messages, **kwargs) -> TurnEngine:
    return TurnEngine(
        provider=Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
        **kwargs,
    )


# -- the pure projection -----------------------------------------------------------


def test_the_view_is_the_prefix_plus_the_post_undo_tail():
    marker = {"boundary": 3, "staged_at": 5, "ts": 1.0}
    view = revert_view(CONVERSATION, marker)

    assert [m.get("content") for m in view] == [
        "be helpful",
        "first question",
        "first answer",
        "third question",
        "third answer",
    ]
    assert hidden_count(marker) == 2
    assert marker_boundary(marker) == 3


def test_a_tail_that_does_not_open_with_a_user_turn_is_dropped():
    """The window between staging and the next send can catch an interrupted turn's
    debris; a branch opening mid-step is not a request any provider accepts."""
    marker = {"boundary": 1, "staged_at": 3, "ts": 1.0}
    messages = CONVERSATION[:3] + [
        {"role": "notice", "kind": "interrupted"},
        {"role": "assistant", "content": "half an answer"},
    ]

    assert [m.get("content") for m in revert_view(messages, marker)] == ["be helpful"]


def test_no_marker_is_the_identity():
    assert revert_view(CONVERSATION, None) == CONVERSATION
    assert revert_view(CONVERSATION, {}) == CONVERSATION
    assert compaction_fits(None, None) is True


def test_visible_user_indexes_respect_the_boundary():
    assert visible_user_indexes(CONVERSATION, None) == [1, 3, 5]
    assert visible_user_indexes(CONVERSATION, {"boundary": 4, "staged_at": 7}) == [1, 3]


# -- the engine view ---------------------------------------------------------------


def test_outbound_truncates_at_the_marker_and_leaves_the_transcript_alone(tmp_path):
    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION])
    engine.revert_state = {"boundary": 3, "staged_at": 7, "ts": 1.0}

    out = engine._outbound_messages()

    assert [m.get("content") for m in out] == ["be helpful", "first question", "first answer"]
    # The canonical thread is complete — the marker is a projection, not a deletion.
    assert len(engine.messages) == len(CONVERSATION)


def test_outbound_sends_the_post_undo_tail_after_the_kept_prefix(tmp_path):
    """A destructive cleanup would delete the undone span so the model sees prefix + new message;
    with an additive marker the same view is prefix + `messages[staged_at:]`."""
    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION[:3]])
    engine.revert_state = {"boundary": 1, "staged_at": 3, "ts": 1.0}
    engine.messages.append({"role": "user", "content": "the edited question"})
    engine.messages.append({"role": "assistant", "content": "the new answer"})

    out = engine._outbound_messages()

    assert [m.get("content") for m in out] == [
        "be helpful",
        "the edited question",
        "the new answer",
    ]
    # Nothing was removed from the record.
    assert any(m.get("content") == "first question" for m in engine.messages)


def test_a_compaction_that_would_summarize_hidden_messages_is_suspended(tmp_path):
    from harness.context.compaction import CompactionState

    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION])
    engine.revert_state = {"boundary": 3, "staged_at": 7, "ts": 1.0}
    engine.compaction_state = CompactionState(
        boundary_index=5, summary_text="secret summary of hidden turns", working_state=""
    )

    out = engine._outbound_messages()

    assert not any("<compacted-history>" in str(m.get("content")) for m in out)
    assert [m.get("content") for m in out] == ["be helpful", "first question", "first answer"]


def test_a_compaction_inside_the_kept_prefix_still_applies(tmp_path):
    from harness.context.compaction import CompactionState

    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION])
    engine.revert_state = {"boundary": 5, "staged_at": 7, "ts": 1.0}
    engine.compaction_state = CompactionState(
        boundary_index=3, summary_text="a summary of the early turns", working_state=""
    )

    out = engine._outbound_messages()

    assert "<compacted-history>" in str(out[1].get("content"))
    # The block stands in for the first turns; the kept prefix's tail (user 2's turn)
    # follows, and the post-undo tail is empty (staged at the transcript's end).
    assert [m.get("content") for m in out[2:]] == [
        "second question",
        "second answer",
    ]


# -- the session verbs -------------------------------------------------------------


def test_revert_stages_the_last_user_message_and_returns_it_for_the_draft(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s1", [dict(m) for m in CONVERSATION])

    out = mgr.revert_session("s1")

    assert out["ok"] is True
    assert out["revert"]["boundary"] == 5 and out["revert"]["staged_at"] == 7
    assert out["message"]["text"] == "third question"
    assert mgr.session_store.load("s1").revert == out["revert"]
    # A live engine, if any, sees the marker at once.
    assert mgr.revert_marker("s1") == out["revert"]


def test_repeating_undo_walks_back_and_refuses_when_nothing_visible_is_left(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s2", [dict(m) for m in CONVERSATION])

    assert mgr.revert_session("s2")["message"]["text"] == "third question"
    second = mgr.revert_session("s2")
    assert second["ok"] is True and second["revert"]["boundary"] == 3
    assert second["message"]["text"] == "second question"
    third = mgr.revert_session("s2")
    assert third["ok"] is True and third["revert"]["boundary"] == 1
    assert third["message"]["text"] == "first question"

    refused = mgr.revert_session("s2")
    assert refused["ok"] is False and "nothing left to undo" in refused["error"]


def test_undo_can_name_a_boundary_by_index(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s3", [dict(m) for m in CONVERSATION])

    out = mgr.revert_session("s3", message_id=3)

    assert out["ok"] is True and out["revert"]["boundary"] == 3
    assert out["message"]["text"] == "second question"
    assert mgr.revert_session("s3", message_id=2)["ok"] is False  # not a user message


def test_redo_walks_forward_and_then_clears(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s4", [dict(m) for m in CONVERSATION])
    mgr.revert_session("s4", message_id=1)

    step = mgr.unrevert_session("s4")
    assert step["ok"] is True and step["cleared"] is False
    assert step["revert"]["boundary"] == 3 and step["message"]["text"] == "second question"

    step = mgr.unrevert_session("s4")
    assert step["cleared"] is False and step["revert"]["boundary"] == 5

    cleared = mgr.unrevert_session("s4")
    assert cleared["ok"] is True and cleared["cleared"] is True
    assert cleared["revert"] is None
    assert mgr.session_store.load("s4").revert == {}
    assert mgr.unrevert_session("s4")["ok"] is False  # nothing to redo anymore


def test_revert_refuses_with_no_user_messages(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s5", [{"role": "system", "content": "be helpful"}])

    out = mgr.revert_session("s5")

    assert out["ok"] is False and "nothing to undo" in out["error"]
    assert mgr.unrevert_session("s5")["ok"] is False
    assert mgr.revert_session("nobody")["ok"] is False


def test_revert_while_running_interrupts_the_turn_first(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s6", [dict(m) for m in CONVERSATION])
    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION])
    mgr._engines["s6"] = engine
    mgr.mark_running("s6")
    try:
        out = mgr.revert_session("s6")
    finally:
        mgr.mark_idle("s6")

    assert out["ok"] is True
    assert engine._cancel.is_set() is True  # request_interrupt pulled the Stop rail
    assert engine.revert_state == out["revert"]


def test_redo_is_refused_mid_turn(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s7", [dict(m) for m in CONVERSATION])
    mgr.revert_session("s7", message_id=1)
    mgr.mark_running("s7")
    try:
        out = mgr.unrevert_session("s7")
    finally:
        mgr.mark_idle("s7")

    assert out["ok"] is False and "mid-turn" in out["error"]


def test_the_marker_survives_the_per_turn_save(tmp_path, monkeypatch):
    """`manager.save` rebuilds the record without `revert`; the upsert must leave the
    column alone (the same rule `kind`/`team` follow)."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s8", [dict(m) for m in CONVERSATION])
    staged = mgr.revert_session("s8")["revert"]

    engine = _engine(tmp_path, [dict(m) for m in CONVERSATION])
    mgr.save("s8", engine)

    assert mgr.session_store.load("s8").revert == staged


def test_a_reloaded_engine_keeps_the_marker(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s9", [dict(m) for m in CONVERSATION])
    staged = mgr.revert_session("s9")["revert"]

    engine = mgr.get_engine("s9")

    assert engine is not None and engine.revert_state == staged


def test_a_fork_does_not_inherit_the_marker(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s10", [dict(m) for m in CONVERSATION])
    mgr.revert_session("s10")

    out = mgr.fork_session("s10")

    assert out["ok"] is True
    assert mgr.session_store.load(out["session_id"]).revert == {}


# -- the routes --------------------------------------------------------------------


def test_the_routes_are_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "route-1", [dict(m) for m in CONVERSATION])
    client = TestClient(create_app(mgr))

    resp = client.post("/v1/sessions/route-1/revert")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["message"]["text"] == "third question"

    listed = client.get("/v1/sessions/route-1/messages").json()
    assert listed["revert"] == body["revert"]

    resp = client.post("/v1/sessions/route-1/unrevert")
    assert resp.status_code == 200 and resp.json()["revert"] is None
    assert client.get("/v1/sessions/route-1/messages").json()["revert"] is None

    refused = client.post("/v1/sessions/route-1/unrevert").json()
    assert refused["ok"] is False


def test_the_route_accepts_an_explicit_boundary(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "route-2", [dict(m) for m in CONVERSATION])
    client = TestClient(create_app(mgr))

    body = client.post(
        "/v1/sessions/route-2/revert", json={"message_id": 1}
    ).json()

    assert body["ok"] is True and body["revert"]["boundary"] == 1


def test_a_turn_runs_over_the_truncated_view(tmp_path, monkeypatch):
    """End to end at the engine seam: after a staged revert the provider receives the
    prefix only, while the record on disk keeps every message."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "s11", [dict(m) for m in CONVERSATION])
    engine = mgr.get_engine("s11")
    assert engine is not None

    mgr.revert_session("s11")

    sent = {}

    class CapturingProvider(Provider):
        def complete(self, *, model, messages, tools=None, **settings):
            sent["messages"] = [dict(m) for m in messages]
            return AssistantTurn(text="ok", finish_reason="stop")

    engine.provider = CapturingProvider()

    async def go():
        return [event async for event in engine.run("a new question")]

    asyncio.run(go())

    contents = [m.get("content") for m in sent["messages"]]
    assert "third question" not in contents
    assert any("a new question" in str(c) for c in contents)
    assert any(m.get("content") == "third question" for m in engine.messages)

# -- branch fencing: a completion from the undone span is dropped ----------------------


def _deliver(mgr, parent_id: str, child_id: str, report: str = "findings") -> None:
    """Drive the background-completion path directly — the manager method the child's
    completion writer calls."""
    asyncio.run(
        mgr._inject_background_result(
            parent_id=parent_id,
            child_id=child_id,
            description="investigate the cache",
            result={"status": "completed", "report": report},
        )
    )


def _messages(mgr, sid: str) -> list[dict]:
    record = mgr.session_store.load(sid)
    return list(record.messages) if record else []


def test_a_background_completion_lands_when_no_undo_happened(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "p1", CONVERSATION)
    mgr._note_background_child("c1", "p1")
    before = len(_messages(mgr, "p1"))

    _deliver(mgr, "p1", "c1")

    after = _messages(mgr, "p1")
    assert len(after) > before, "the envelope reached the parent"
    assert any("findings" in str(m.get("content")) for m in after)


def test_a_completion_from_before_an_undo_is_dropped(tmp_path, monkeypatch):
    """Branch fencing: the child was born in the span the person just hid, so its
    result must not reopen the parent with news about an undone task."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "p1", CONVERSATION)
    mgr._note_background_child("c1", "p1")
    assert mgr.revert_session("p1")["ok"] is True
    before = len(_messages(mgr, "p1"))

    _deliver(mgr, "p1", "c1")

    assert len(_messages(mgr, "p1")) == before, "nothing was injected"


def test_a_child_born_after_the_undo_is_delivered(tmp_path, monkeypatch):
    """The tail after the boundary is visible work, so a child started there is not stale."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "p1", CONVERSATION)
    assert mgr.revert_session("p1")["ok"] is True
    mgr._note_background_child("c2", "p1")  # born in the visible tail
    before = len(_messages(mgr, "p1"))

    _deliver(mgr, "p1", "c2")

    assert len(_messages(mgr, "p1")) > before


def test_a_second_undo_still_fences_a_child_born_after_the_first(tmp_path, monkeypatch):
    """The generation is a counter, not the boundary index: two undos at the same message
    count still mean two different spans."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "p1", CONVERSATION)
    assert mgr.revert_session("p1")["ok"] is True
    mgr._note_background_child("c2", "p1")
    assert mgr.revert_session("p1")["ok"] is True
    before = len(_messages(mgr, "p1"))

    _deliver(mgr, "p1", "c2")

    assert len(_messages(mgr, "p1")) == before


def test_the_generation_is_cleared_with_the_child(tmp_path, monkeypatch):
    """The generation entry is consumed by the delivery itself; the parent/loop/task maps
    are cleared by the completion writer's own finally (which a direct call bypasses)."""
    mgr = _manager(tmp_path, monkeypatch)
    _record(mgr, "p1", CONVERSATION)
    mgr._note_background_child("c1", "p1")
    _deliver(mgr, "p1", "c1")
    assert "c1" not in mgr._background_generation
