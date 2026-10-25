"""The model-visibility invariant (invariants.py): everything the model is about to see
has left a trace in the session log — "model-visible means logged", the invariant in
the audit shape reeve's beside-the-messages log allows.

The engine-level tests here double as regression pins for the paths the audit's first
pass found unlogged: steering injections, the approver flows' tool results, interrupted
and denied tool results, and partial assistant messages from a failed stream. A new
injection path that forgets to log is caught by the record — and, under
`invariant_mode="strict"`, by the raise.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.loop import TurnEngine
from harness.session.invariants import InvariantError, ModelVisibilityAudit, _empty_index, _index_events, audit_messages, audit_workflow_log, open_workflow_runs
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.base import ToolCall
from harness.session.session_log import NULL_LOG, SessionLog
from harness.tools import ToolRegistry
from harness.session.turns import ATTACHED_BY, attachment_message


def _index(*events: dict) -> dict:
    index = _empty_index()
    _index_events(index, list(events))
    return index


def _header(seq=0):
    return {"seq": seq, "type": "request/header", "reason": "initial"}


def _user(seq=1, content="hi"):
    return {"seq": seq, "type": "user/message", "content": content}


def _assistant(seq=2, text="ok"):
    return {"seq": seq, "type": "assistant/message", "text": text}


def _tool_result(seq=3, call_id="c1"):
    return {"seq": seq, "type": "tool/result", "call_id": call_id, "status": "ok"}


# -- audit_messages (pure) ---------------------------------------------------------


def test_fully_logged_history_is_clean():
    index = _index(_header(), _user(), _assistant())
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    assert audit_messages(messages, index) == []


def test_a_logged_tool_result_covers_its_message_and_an_unlogged_one_is_flagged():
    messages = [{"role": "tool", "tool_call_id": "c1", "content": "out"}]
    assert audit_messages(messages, _index(_tool_result(call_id="c1"))) == []
    violations = audit_messages(messages, _index(_tool_result(call_id="other")))
    assert len(violations) == 1 and "c1" in violations[0]


def test_counted_classes_flag_surplus_messages():
    index = _index(_user(), _assistant())
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "assistant", "content": "surprise"},
    ]
    violations = audit_messages(messages, index)
    assert any("assistant message" in v for v in violations)


def test_user_and_steering_share_one_coverage_class():
    """A steering injection is model-visible text outside the user's typed turn; logged
    as `steering/message`, it counts toward the same coverage the turn does."""
    message = {"role": "user", "content": "focus", "ts": 0.0}
    assert audit_messages([message], _index(_user())) == []
    steering = {"seq": 9, "type": "steering/message", "text": "focus"}
    assert audit_messages([message], _index(steering)) == []


@pytest.mark.parametrize(
    "tag,event_type",
    [
        ("verify_nudge", "verify/nudge"),
        ("todo_nudge", "todo/nudge"),
        ("checkpoint", "turn/checkpoint"),
    ],
)
def test_injected_prompts_need_their_own_event(tag, event_type):
    message = {"role": "user", "content": "x", ATTACHED_BY: tag}
    assert audit_messages([message], _index()), f"unlogged {tag} must flag"
    assert audit_messages([message], _index({"seq": 9, "type": event_type})) == [], (
        f"logged {tag} must cover"
    )


def test_attachments_are_covered_by_their_calls_result_event():
    message = attachment_message(
        "c7", [{"type": "image_url", "image_url": {"url": "x"}}]
    )
    assert audit_messages([message], _index(_tool_result(call_id="c7"))) == []
    violations = audit_messages([message], _index())
    assert len(violations) == 1 and "c7" in violations[0]


def test_notices_are_not_model_visible():
    assert audit_messages([{"role": "notice", "kind": "error", "text": "x"}], _index()) == []


def test_a_system_prompt_without_a_header_is_flagged():
    violations = audit_messages([{"role": "system", "content": "s"}], _index())
    assert len(violations) == 1 and "request/header" in violations[0]


# -- ModelVisibilityAudit (incremental, against a real log) ------------------------


def test_check_advances_incrementally_and_recovers(tmp_path):
    log = SessionLog(tmp_path / "events.jsonl")
    log.append("user/message", content="hi")
    audit = ModelVisibilityAudit()

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    flagged = audit.check(log, messages)
    assert any("assistant message" in v for v in flagged)

    log.append("assistant/message", text="ok")
    assert audit.check(log, messages) == []
    # A third check with no new events must stay clean — the watermark advanced, the
    # user event was not counted twice.
    assert audit.check(log, messages) == []


def test_a_swapped_log_resets_the_watermark(tmp_path):
    first = SessionLog(tmp_path / "a.jsonl")
    first.append("user/message", content="hi")
    audit = ModelVisibilityAudit()
    assert audit.check(first, [{"role": "user", "content": "hi"}]) == []

    second = SessionLog(tmp_path / "b.jsonl")
    second.append("user/message", content="next")
    # Against the new file the same single message is covered — the index is defined
    # over one log, and the old file's events do not bleed into it.
    assert audit.check(second, [{"role": "user", "content": "next"}]) == []


def test_a_disabled_log_audits_vacuously():
    assert (
        ModelVisibilityAudit().check(NULL_LOG, [{"role": "assistant", "content": "?"}])
        == []
    )


def test_active_compaction_without_a_summary_event_is_flagged(tmp_path):
    log = SessionLog(tmp_path / "events.jsonl")
    log.append("user/message", content="hi")
    audit = ModelVisibilityAudit()
    violations = audit.check(
        log, [{"role": "user", "content": "hi"}], compaction_active=True
    )
    assert any("compaction/summary" in v for v in violations)


# -- the engine --------------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def stream(self, *, model, messages, tools=None, **settings):
        # One turn per request: a second chunk would REPLACE the first as the step's
        # turn, not extend it.
        if self._turns:
            yield StreamChunk(turn=self._turns.pop(0))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, **kwargs):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        **kwargs,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    return engine


def _run(engine, text="hello"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def _violations(log):
    return [e for e in log.read() if e["type"] == "invariant/violation"]


def test_a_tool_turn_leaves_the_log_fully_covering_the_request(tmp_path):
    """One scripted turn through the denial path (the registry is empty, so the call is
    refused) — every message the model saw must be in the log, or the audit says so."""
    engine = _engine(
        tmp_path,
        _Scripted(
            [
                AssistantTurn(tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
    )
    _run(engine)
    assert _violations(engine.log) == []
    kinds = {e["type"] for e in engine.log.read()}
    assert {"user/message", "assistant/message", "tool/result"} <= kinds
    # The call never STARTED (denied before dispatch, where `_log_call` runs), so the
    # the log names it through its result alone: status `denied`, same call id.
    assert "tool/call" not in kinds
    denied = next(e for e in engine.log.read() if e["type"] == "tool/result")
    assert denied["status"] == "denied" and denied["call_id"] == "c1"


def test_an_unlogged_injection_is_recorded_once_per_distinct_gap(tmp_path):
    """The bug class in one frame: someone appends to `self.messages` and forgets the
    log. The audit names it once, not once per request."""
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")])
    )
    _run(engine)
    assert _violations(engine.log) == []

    engine.messages.append({"role": "user", "content": "unlogged", "ts": 0.0})
    engine._audit_model_visibility()
    engine._audit_model_visibility()  # the identical gap is not re-reported
    recorded = _violations(engine.log)
    assert len(recorded) == 1
    assert "user message" in recorded[0]["violations"][0]


def test_a_gap_is_reported_once_while_later_gaps_join_it(tmp_path):
    """A gap stays in every later request. Comparing whole lists re-reported it whenever another
    joined, and a counted line changed number at every step: 235 rows for one gap (2026-10-15)."""
    engine = _engine(tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")]))
    _run(engine)
    engine.messages.append({"role": "assistant", "content": "unlogged one"})
    engine._audit_model_visibility()
    engine.messages.append({"role": "assistant", "content": "unlogged two"})
    engine._audit_model_visibility()  # "2 assistant message(s)": the same gap, counted higher
    engine.messages.append({"role": "tool", "tool_call_id": "c9", "content": "x"})
    engine._audit_model_visibility()  # a new gap joins: only it is reported
    recorded = [row["violations"] for row in _violations(engine.log)]
    assert len(recorded) == 2
    assert "assistant message" in recorded[0][0]
    assert recorded[1] == [next(v for v in recorded[1] if "c9" in v)]


def test_strict_mode_raises_on_the_gap_instead_of_recording(tmp_path):
    engine = _engine(
        tmp_path,
        _Scripted([AssistantTurn(text="ok", finish_reason="stop")]),
        invariant_mode="strict",
    )
    _run(engine)
    engine.messages.append({"role": "assistant", "content": "unlogged"})
    with pytest.raises(InvariantError):
        engine._audit_model_visibility()


def test_steering_reaches_the_log(tmp_path):
    engine = _engine(tmp_path, _Scripted([]))
    engine._steering = [("focus on the flaky test", "slack", None)]
    engine._inject_steering()

    assert engine.messages[-1]["content"] == "focus on the flaky test"
    events = [e for e in engine.log.read() if e["type"] == "steering/message"]
    assert len(events) == 1 and events[0]["source"] == "slack"


def test_steering_carries_the_display_sidecar(tmp_path):
    """A steered force-run shows the person's literal line, not the model-facing
    framing: `queue_steering` takes the same `display` a fresh `run` does, and the
    injected message must carry the `_display` sidecar."""
    engine = _engine(tmp_path, _Scripted([]))
    engine.queue_steering("Use the skill \"review\"…", None, "/review the diff")
    engine._inject_steering()

    assert engine.messages[-1]["_display"] == "/review the diff"
    events = [e for e in engine.log.read() if e["type"] == "steering/message"]
    assert events[0]["display"] == "/review the diff"


def test_a_steered_message_is_marked_for_the_transcript_only(tmp_path):
    """`_steer` is what makes the GUI draw the message as an in-turn note; the provider
    must never see the marker (unknown keys are provider-rejected and it is not data)."""
    engine = _engine(tmp_path, _Scripted([]))
    engine.queue_steering("tôi là ai?")
    engine._inject_steering()

    assert engine.messages[-1]["_steer"] is True
    outbound = engine._outbound_messages()
    assert all("_steer" not in m for m in outbound)


def test_a_stream_dying_before_any_text_leaves_no_partial(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))

    class _DiesImmediately(ProviderClient):
        def stream(self, *, model, messages, tools=None, **settings):
            raise ValueError("permanent failure")

        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = _engine(tmp_path, _DiesImmediately())
    _run(engine)
    assert not [
        e
        for e in engine.log.read()
        if e["type"] == "assistant/message" and e.get("partial")
    ]
    assert _violations(engine.log) == []


def test_a_partial_the_user_watched_is_covered(tmp_path, monkeypatch):
    """Text streamed, THEN the stream died: the partial survives into history, and from
    this change on it survives into the log too — before, it was model-visible and
    unlogged, the exact gap the invariant exists to name."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))

    class _DiesMidText(ProviderClient):
        def stream(self, *, model, messages, tools=None, **settings):
            yield StreamChunk(text_delta="half an ans")
            raise ValueError("permanent failure")

        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = _engine(tmp_path, _DiesMidText())
    _run(engine)

    partials = [
        e
        for e in engine.log.read()
        if e["type"] == "assistant/message" and e.get("partial")
    ]
    assert len(partials) == 1 and partials[0]["text"] == "half an ans"
    assert _violations(engine.log) == []


# -- the workflow vocabulary ---------------------------------------------------------
#
# The model-visibility audit above checks the MESSAGE side. This one checks the workflow
# events the run-view node, a `resume` and the journal all read: a run that cannot have
# happened (an end with no start, a replay that also dispatched, tallies that disagree)
# must be named, while an interrupted run (start, no end) is the log working.

def _wf_start(root="w1", **extra):
    return {"type": "workflow/run-start", "root_call_id": root, "name": "wf", **extra}


def _wf_end(root="w1", **extra):
    return {"type": "workflow/run-end", "root_call_id": root, "status": "ok", **extra}


def _wf_note(root="w1", text="phase"):
    return {"type": "workflow/note", "root_call_id": root, "kind": "phase", "text": text}


def _wf_dispatch(root="w1", call="w1:code:1", **extra):
    return {
        "type": "tool/code-dispatch",
        "root_call_id": root,
        "call_id": call,
        "name": "task",
        **extra,
    }


def _wf_dispatch_start(root="w1", call="w1:code:1"):
    return {
        "type": "tool/code-dispatch-start",
        "root_call_id": root,
        "call_id": call,
        "name": "task",
        "arguments": "{}",
    }


def test_a_well_formed_run_has_no_violations():
    events = [
        _wf_start(),
        _wf_note(),
        _wf_dispatch_start(call="w1:code:1"),
        _wf_dispatch(call="w1:code:1"),
        _wf_dispatch(call="w1:code:2", replayed=True),
        _wf_end(agents=2, replayed=1),
    ]
    assert audit_workflow_log(events) == []
    assert open_workflow_runs(events) == []


def test_an_interrupted_run_is_open_not_corrupt():
    events = [
        _wf_start(root="w1"),
        _wf_dispatch_start(root="w1", call="w1:code:1"),
        _wf_dispatch(root="w1", call="w1:code:1"),
    ]
    assert audit_workflow_log(events) == []
    assert open_workflow_runs(events) == ["w1"]


def test_events_for_an_unknown_run_are_named():
    events = [
        _wf_note(root="w9"),
        _wf_end(root="w9"),
        _wf_dispatch(root="w9", call="w9:code:1"),
    ]
    violations = audit_workflow_log(events)
    assert any("note for w9 with no run-start" in v for v in violations)
    assert any("run-end for w9 with no run-start" in v for v in violations)
    # A run_code-shaped dispatch under an unknown root is simply not this vocabulary:
    # the same event stream carries `run_code` sub-calls with no workflow at all.
    assert not any("w9:code:1" in v for v in violations)


def test_a_second_end_and_a_dispatch_after_it_are_named():
    events = [
        _wf_start(),
        _wf_dispatch_start(),
        _wf_dispatch(),
        _wf_end(agents=1),
        _wf_end(agents=1),
        _wf_dispatch(call="w1:code:2"),
    ]
    violations = audit_workflow_log(events)
    assert any("written twice" in v for v in violations)
    assert any("after its run-end" in v for v in violations)


def test_a_live_dispatch_must_have_started_and_a_replay_must_not_have():
    events = [
        _wf_start(),
        _wf_dispatch(call="w1:code:1"),
        _wf_dispatch_start(call="w1:code:2"),
        _wf_dispatch(call="w1:code:2", replayed=True),
    ]
    violations = audit_workflow_log(events)
    assert any("w1:code:1' for w1 with no dispatch-start" in v for v in violations)
    assert any("also has a dispatch-start" in v for v in violations)


def test_the_end_tallies_must_match_the_events():
    events = [
        _wf_start(),
        _wf_dispatch_start(call="w1:code:1"),
        _wf_dispatch(call="w1:code:1"),
        _wf_end(agents=3, replayed=1),
    ]
    violations = audit_workflow_log(events)
    assert any("reports 3 agent(s) but the log holds 1" in v for v in violations)
    assert any("reports 1 replay(es) but the log holds 0" in v for v in violations)

