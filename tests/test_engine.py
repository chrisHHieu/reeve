"""The turn engine: what a turn does and says, in what order, when tools run, ask, fail or repeat,
what each request carries, and the reminders and limits that keep a turn honest.

The model is scripted (`ScriptedProvider`), the tools are the files toolkit over a temp folder or
plain functions, and nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import time
from pathlib import Path

import aisuite as ai
from aisuite.agents import ToolMetadata

from harness.context import compaction
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall
from harness.loop import TRUNCATION_CONTINUES, ApprovalOutcome, PermissionRequest, TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)], finish_reason="tool_calls")


def _calls_turn(*calls):
    return AssistantTurn(tool_calls=[ToolCall(id=f"call_{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)], finish_reason="tool_calls")


class ScriptedProvider(ProviderClient):
    """Answers with the queued turns in order, or with the first one forever when `loop`."""

    def __init__(self, turns, *, loop=False):
        self._turns = list(turns)
        self._loop = loop
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turns[0] if self._loop else self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns, *, approver=None, loop=False, max_iterations=12):
    """An engine with the files toolkit over `tmp_path`, and the scripted model behind it."""
    model = ScriptedProvider(turns, loop=loop)
    files = ToolRegistry()
    files.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(provider=model, registry=files, permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5", approver=approver, max_iterations=max_iterations), model


def _plain_engine(tmp_path, turns):
    registry = ToolRegistry()
    engine = TurnEngine(provider=ScriptedProvider(turns), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5")
    return engine, registry


def _collect(engine, user_input):
    async def run():
        return [event async for event in engine.run(user_input)]

    return asyncio.run(run())


def _types(events):
    return [event.type for event in events]


def _answer(outcome):
    async def answer(request: PermissionRequest):
        return outcome

    return answer


def _image_message():
    return {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}


class _Blind(ScriptedProvider):
    def capabilities(self, model):
        return ModelCapabilities(vision=False)


# -- a turn --------------------------------------------------------------------------------------


def test_a_turn_without_tools_starts_answers_and_ends(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("all done")])
    events = _collect(engine, "hi")
    assert _types(events) == [EventType.TURN_START, EventType.ASSISTANT_MESSAGE, EventType.TURN_END]
    assert events[1].data["text"] == "all done" and events[-1].data["status"] == "completed"


def test_a_read_runs_unasked_and_its_result_is_in_the_history(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _ = _engine(tmp_path, [_tool_turn("read_file", {"path": "a.txt"}), _text_turn("it says hello")])
    events = _collect(engine, "read a.txt")
    assert _types(events) == [
        EventType.TURN_START, EventType.ASSISTANT_MESSAGE, EventType.TOOL_PROPOSED, EventType.TOOL_STARTED,
        EventType.TOOL_FINISHED, EventType.ITERATION_END, EventType.ASSISTANT_MESSAGE, EventType.TURN_END,
    ]
    assert next(e for e in events if e.type == EventType.TOOL_FINISHED).data["status"] == "ok"
    assert any(m.get("role") == "tool" and "hello" in m["content"] for m in engine.messages)


def test_a_write_asks_and_runs_once_approved(tmp_path):
    engine, _ = _engine(tmp_path, [_tool_turn("write_file", {"path": "new.py", "content": "print(1)\n"}), _text_turn("wrote new.py")], approver=_answer(ApprovalOutcome.ONCE))
    assert EventType.PERMISSION_REQUIRED in _types(_collect(engine, "create new.py"))
    assert (tmp_path / "new.py").read_text() == "print(1)\n"


def test_a_denied_write_does_not_run_and_the_turn_goes_on(tmp_path):
    engine, _ = _engine(tmp_path, [_tool_turn("write_file", {"path": "new.py", "content": "x"}), _text_turn("ok, skipped it")], approver=_answer(ApprovalOutcome.DENY))
    events = _collect(engine, "create new.py")
    assert not (tmp_path / "new.py").exists()
    assert next(e for e in events if e.type == EventType.TOOL_FINISHED).data["status"] == "denied"
    assert _types(events)[-1] == EventType.TURN_END
    assert any(m.get("role") == "tool" and "not executed" in m["content"] for m in engine.messages)


def test_a_stop_during_a_step_ends_the_turn_before_the_next_model_call(tmp_path):
    holder = {}

    async def approve_then_stop(request):
        holder["engine"].request_interrupt()
        return ApprovalOutcome.ONCE

    engine, provider = _engine(tmp_path, [_tool_turn("write_file", {"path": "x.py", "content": "x"}), _text_turn("should not be reached")], approver=approve_then_stop)
    holder["engine"] = engine
    assert _collect(engine, "do a thing")[-1].type == EventType.INTERRUPTED
    assert provider.calls == 1


def test_a_queued_message_is_read_by_the_same_turn(tmp_path):
    engine, provider = _engine(tmp_path, [_text_turn("first"), _text_turn("second")])
    engine.queue_steering("actually, also do this")
    events = _collect(engine, "do the first thing")
    assert provider.calls == 2 and events[-1].data["status"] == "completed"
    assert any(m.get("role") == "user" and m["content"] == "actually, also do this" for m in engine.messages)


def test_a_streamed_answer_arrives_as_deltas_then_the_message(tmp_path):
    class Streams(ProviderClient):
        def complete(self, **kwargs):  # pragma: no cover - streamed instead
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

        def stream(self, *, model, messages, tools=None, **settings):
            for piece in ["Hel", "lo, ", "world"]:
                yield StreamChunk(text_delta=piece)
            yield StreamChunk(turn=AssistantTurn(text="Hello, world", finish_reason="stop"))

    engine = TurnEngine(provider=Streams(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5")
    events = _collect(engine, "say hi")
    assert [e.data["text"] for e in events if e.type == EventType.ASSISTANT_DELTA] == ["Hel", "lo, ", "world"]
    assert next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["text"] == "Hello, world"
    assert events[-1].type == EventType.TURN_END


# -- several calls in one step -------------------------------------------------------------------


def test_low_risk_calls_of_one_step_run_at_the_same_time(tmp_path):
    """Each waits for the other at a barrier: run one after the other, the first would time out."""
    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def side_a():
        """Wait for side_b."""
        barrier.wait()
        return {"side": "a"}

    def side_b():
        """Wait for side_a."""
        barrier.wait()
        return {"side": "b"}

    engine, registry = _plain_engine(tmp_path, [_calls_turn(("side_a", {}), ("side_b", {})), _text_turn("done")])
    registry.register(side_a, metadata=low)
    registry.register(side_b, metadata=low)
    finished = [e for e in _collect(engine, "go") if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 2 and all(e.data["status"] == "ok" for e in finished)
    assert {m.get("tool_call_id") for m in engine.messages if m.get("role") == "tool"} == {"call_0", "call_1"}


def test_other_calls_of_one_step_run_in_order(tmp_path):
    """The slow one goes first; were they run together, the quick one would finish inside it."""
    log: list[str] = []
    medium = ai.ToolMetadata(category="filesystem", risk_level="medium", requires_approval=False)

    def slow():
        """Takes a moment."""
        log.append("slow begins")
        time.sleep(0.2)
        log.append("slow ends")
        return "slow done"

    def quick():
        """Takes no time."""
        log.extend(["quick begins", "quick ends"])
        return "quick done"

    engine, registry = _plain_engine(tmp_path, [_calls_turn(("slow", {}), ("quick", {})), _text_turn("done")])
    for tool in (slow, quick):
        registry.register(tool, metadata=medium)
    _collect(engine, "run both")
    assert log == ["slow begins", "slow ends", "quick begins", "quick ends"]


# -- what a request carries ----------------------------------------------------------------------


def _pdf_part():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    return {"type": "file", "file": {"filename": "d.pdf", "file_data": "data:application/pdf;base64," + base64.b64encode(buffer.getvalue()).decode()}}


def test_a_pdf_is_named_to_a_model_that_cannot_read_one_and_the_history_keeps_it(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])  # default capabilities: no PDF
    engine.messages.append({"role": "user", "content": [{"type": "text", "text": "read this"}, _pdf_part()]})
    sent = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "file" for p in sent) and "d.pdf" in sent[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "file"


def test_a_pdf_is_sent_as_it_is_to_a_model_that_reads_one(tmp_path):
    class Reads(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=True, pdf=True)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = Reads([_text_turn("ok")])
    engine.messages.append({"role": "user", "content": [{"type": "text", "text": "read this"}, _pdf_part()]})
    assert engine._outbound_messages()[-1]["content"][1]["type"] == "file"


def test_an_image_is_named_to_a_model_that_cannot_see_and_the_history_keeps_it(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = _Blind([_text_turn("ok")])
    engine.messages.append(_image_message())
    sent = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "image_url" for p in sent) and "not viewable" in sent[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "image_url"


def _audio_file_part():
    url = "data:audio/mpeg;base64," + base64.b64encode(b"ID3fake").decode()
    return {"type": "file", "file": {"filename": "call.mp3", "file_data": url}, "saved_at": "C:/session/call.mp3"}


def test_outbound_names_audio_for_models_that_cannot_hear_and_keeps_it_for_gemini(tmp_path):
    """Audio and video ride the `file` part a PDF does. A model that cannot take them gets a
    sentence with the saved path; one that can gets the recording; the history is untouched."""

    class HearingProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=True, pdf=True, audio=True, video=True)

        def media_carry(self, model, mime):
            return True

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.messages.append({"role": "user", "content": [{"type": "text", "text": "listen"}, _audio_file_part()]})
    deaf = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "file" for p in deaf)
    assert "call.mp3" in deaf[-1]["text"] and "C:/session/call.mp3" in deaf[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "file"  # history untouched
    engine.provider = HearingProvider([_text_turn("ok")])
    assert engine._outbound_messages()[-1]["content"][1]["type"] == "file"


def test_a_providers_own_sidecar_is_kept_on_the_message_and_sent_back_to_it(tmp_path):
    """Gemini's thought signatures go back to Gemini; another provider strips them itself.
    Display-only keys are still left out."""
    sidecar = {"text_sig": "c2ln", "call_sigs": []}
    engine, _ = _engine(tmp_path, [AssistantTurn(text="ok", finish_reason="stop", extras={"_gemini": sidecar})])
    _collect(engine, "hi")
    assert engine.messages[-1]["_gemini"] == sidecar
    sent = engine._outbound_messages()[-1]
    assert sent["_gemini"] == sidecar and "ts" not in sent


# -- switching model -----------------------------------------------------------------------------


def test_a_switch_is_marked_only_once_the_conversation_has_begun(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    assert engine.switch_model("zai:glm-5.2") is None and engine.model == "zai:glm-5.2"  # the first bind
    _collect(engine, "hi")
    assert engine.switch_model("zai:glm-5.2") is None  # the same model
    said = engine.switch_model("kimi:kimi-k2.6")
    assert "Kimi K2.6" in said and engine.model == "kimi:kimi-k2.6"
    notice = engine.messages[-1]
    assert (notice["role"], notice["kind"]) == ("notice", "model_switch")
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())


def test_a_switch_to_a_model_that_cannot_see_says_the_images_are_lost_to_it(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = _Blind([_text_turn("ok")])
    engine.messages.append(_image_message())
    assert "images" in engine.switch_model("zai:glm-5.2")


# -- limits --------------------------------------------------------------------------------------


def test_a_turn_that_never_stops_calling_ends_at_its_step_limit_with_a_handoff(tmp_path):
    """One last call without tools writes where it stands, so a follow-up resumes from it."""
    engine, provider = _engine(tmp_path, [_tool_turn("list_files", {}, "c1"), _tool_turn("list_files", {}, "c2"), _tool_turn("list_files", {}, "c3"), _text_turn("DONE: every file listed; nothing remains.")], max_iterations=3)
    end = _collect(engine, "loop forever")[-1]
    assert end.type == EventType.TURN_END and end.data["status"] == "max_iterations_exceeded"
    assert len([m for m in engine.messages if m.get("role") == "assistant" and "DONE:" in str(m.get("content", ""))]) == 1
    assert provider.calls == 4  # three steps and the handoff

    looping, calls = _engine(tmp_path, [_tool_turn("list_files", {})], loop=True, max_iterations=3)
    assert _collect(looping, "loop forever")[-1].data["status"] == "max_iterations_exceeded" and calls.calls == 4


def test_repetition_outliving_the_reminders_stops_the_turn(tmp_path):
    """Eight identical calls with identical results: the reminders said their piece, so
    the turn STOPS with a checkpoint instead of running to the cap."""
    (tmp_path / "a.txt").write_text("same", encoding="utf-8")
    engine, provider = _engine(tmp_path, [_tool_turn("read_file", {"path": "a.txt"})], loop=True, max_iterations=50)
    end = _collect(engine, "read this")[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "repetition_stopped" and end.data["repeated"]["tool"] == "read_file"
    assert provider.calls < 20  # stopped at the repetition, nowhere near the cap


def test_a_length_stopped_answer_is_extended_then_typed_as_truncated(tmp_path):
    """finish_reason=length is a CUT answer: the engine asks for the rest (at most
    TRUNCATION_CONTINUES times), folds the fragments into one message, and — when the
    extension runs out — still reports the cut honestly: the status stays `completed`
    (nothing downstream re-matches) and the payload says `truncated`."""
    engine, provider = _engine(tmp_path, [AssistantTurn(text="half an answer", finish_reason="length")], loop=True)
    end = _collect(engine, "go")[-1]
    assert provider.calls == 1 + TRUNCATION_CONTINUES
    assert end.type == EventType.TURN_END and end.data["status"] == "completed" and end.data["truncated"] is True
    assert engine.messages[-1]["content"] == "half an answer" * (1 + TRUNCATION_CONTINUES)


def test_agent_carries_its_own_step_budget(tmp_path):
    """The agent definition is the extension point: its step budget is a field, and an
    explicit caller override still wins. `None` means uncapped, the default."""
    from harness.tools.task_agents import AgentInfo
    from harness.tools.task_runtime import build_child_engine

    class _Stub:
        def complete(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

    for budget in (17, None):
        child = build_child_engine(workspace=tmp_path, provider=_Stub(), model="m", agent=AgentInfo(name="explore"), max_iterations=budget)
        try:
            assert child.max_iterations == budget
        finally:
            child.executor.close()


# -- the to-do reminder --------------------------------------------------------------------------


def _todo_engine(tmp_path, turns, **kwargs):
    from harness.tools.todo import TodoList, todo_tools

    engine, provider = _engine(tmp_path, turns, **kwargs)
    engine.todo = TodoList()
    engine.registry.register_all(todo_tools(engine.todo))
    return engine, provider


def _distinct_reads(tmp_path, count):
    """Reads of different files, so the repeat guard (at 8) never fires first."""
    for i in range(count):
        (tmp_path / f"a{i}.txt").write_text(f"hello {i}", encoding="utf-8")
    return [_tool_turn("read_file", {"path": f"a{i}.txt"}, f"c{i}") for i in range(count)]


def _reminded(engine):
    return [m for m in engine.messages if m.get("_attached_by") == "todo_nudge"]


def test_todo_nudge_fires_once_after_enough_planless_calls(tmp_path):
    from harness.loop import TODO_NUDGE_AFTER

    engine, _ = _todo_engine(tmp_path, [*_distinct_reads(tmp_path, TODO_NUDGE_AFTER + 2), _text_turn("finished")], max_iterations=TODO_NUDGE_AFTER + 4)
    _collect(engine, "do the thing")
    reminders = [m for m in _reminded(engine) if m.get("role") == "user" and "todo_write" in str(m.get("content", ""))]
    assert len(reminders) == 1  # once, not once per qualifying step


def test_a_short_turn_is_not_nudged(tmp_path):
    """The nudge used to fire at 4 calls. On the real store 72% of the ignored nudges came
    in turns that ended within 3 more calls (the model was right to ignore them); at 10,
    78% of those disappear while 88% of the nudges models acted on stay (2026-09-24)."""
    from harness.loop import TODO_NUDGE_AFTER

    assert TODO_NUDGE_AFTER >= 10
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _ = _todo_engine(tmp_path, [*[_tool_turn("read_file", {"path": "a.txt"}, f"c{i}") for i in range(6)], _text_turn("finished")])
    _collect(engine, "do the thing")
    assert not _reminded(engine)


def test_todo_nudge_stays_silent_once_a_plan_exists(tmp_path):
    from harness.loop import TODO_NUDGE_AFTER

    plan = AssistantTurn(tool_calls=[ToolCall(id="t0", name="todo_write", arguments={"todos": [{"content": "read", "status": "in_progress"}]})], finish_reason="tool_calls")
    # Past the threshold, so the silence is the plan's doing and not the count's.
    engine, _ = _todo_engine(tmp_path, [plan, *_distinct_reads(tmp_path, TODO_NUDGE_AFTER + 2), _text_turn("finished")], max_iterations=TODO_NUDGE_AFTER + 4)
    _collect(engine, "do the thing")
    assert engine._turn_tool_calls > TODO_NUDGE_AFTER and not _reminded(engine)


def test_todo_nudge_never_where_capability_is_absent(tmp_path):
    from harness.loop import TODO_NUDGE_AFTER

    engine, _ = _engine(tmp_path, [*_distinct_reads(tmp_path, TODO_NUDGE_AFTER + 2), _text_turn("finished")], max_iterations=TODO_NUDGE_AFTER + 4)  # todo stays None
    _collect(engine, "do the thing")
    assert engine._turn_tool_calls > TODO_NUDGE_AFTER and not _reminded(engine)


# -- the check after a code change ---------------------------------------------------------------


def test_verification_gate_nudges_once_after_a_code_write(tmp_path):
    """Code changed, nothing ran since — the FIRST completion attempt gets one system
    note and the turn continues; the second attempt goes through."""
    engine, provider = _engine(tmp_path, [_tool_turn("write_file", {"path": "app.py", "content": "x=1"}, "w1"), _text_turn("done!"), _text_turn("done, verified nothing to run")], approver=_answer(ApprovalOutcome.ONCE))
    events = _collect(engine, "fix it")
    assert len([m for m in engine.messages if m.get("_attached_by") == "verify_nudge"]) == 1
    assert provider.calls == 3  # write -> the gated answer -> the post-nudge answer
    assert events[-1].data["status"] == "completed"


def test_verification_gate_stays_silent_without_a_code_write(tmp_path):
    """A notes.md write is prose, not code; a plain answer is not gated."""
    engine, provider = _engine(tmp_path, [_tool_turn("write_file", {"path": "notes.md", "content": "hi"}, "w1"), _text_turn("written")], approver=_answer(ApprovalOutcome.ONCE))
    _collect(engine, "save this")
    assert not any(m.get("_attached_by") == "verify_nudge" for m in engine.messages)
    assert provider.calls == 2  # the write and the answer — no gate call between


def test_evidence_after_the_write_disarms_the_gate(tmp_path):
    engine = _engine(tmp_path, [_text_turn("ok")])[0]
    # The tracking is execution-ordered: a code write resets the clock, an executing
    # tool after it disarms the gate.
    engine._note_tool_outcome("write_file", {"path": "app.py"}, "ok")
    assert engine._turn_wrote_code and not engine._turn_ran_after
    engine._note_tool_outcome("run_shell", {"command": "pytest"}, "ok")
    assert engine._turn_ran_after
    engine._note_tool_outcome("write_file", {"path": "app.py"}, "ok")
    assert not engine._turn_ran_after  # the newer write re-arms the gate


def test_steering_never_splits_an_assistant_from_its_tool_results(tmp_path):
    """P1-9 anchor: steering queued while tools run
    lands at a message boundary that keeps role alternation intact — it may follow a
    tool result, but it may NEVER splice between an assistant message and the tool
    results answering it, and the next provider request starts a clean suffix (the
    cached prefix is untouched)."""
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    engine, provider = _engine(tmp_path, [_tool_turn("read_file", {"path": "a.txt"}, "c1"), _text_turn("here is what I found"), _text_turn("and the extra thing")])
    engine.queue_steering("also check b.txt")  # queued while the tool call runs
    _collect(engine, "read it")
    for i, message in enumerate(engine.messages):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            following = engine.messages[i + 1] if i + 1 < len(engine.messages) else None
            assert following is not None and following.get("role") == "tool", "a user message was spliced between an assistant and its tool result"
    # The steering message rides AFTER every tool result of the step it interrupted.
    steering_at = next(i for i, m in enumerate(engine.messages) if m.get("content") == "also check b.txt")
    results = [i for i, m in enumerate(engine.messages) if m.get("role") == "tool"]
    assert results and all(i < steering_at for i in results)
    # tool step -> steered answer; the third scripted turn stays queued.
    assert provider.calls == 2


# -- P1: aggregate output budget, calibrated token meter ---------


def test_turn_output_budget_folds_the_newest_result_to_a_spill_locator(tmp_path):
    """Forty medium results overflow the window as surely as one huge one: past the
    turn's aggregate cap, the newest result keeps a small head + a spill locator — the
    compaction fold's shape, paid at write time."""
    from harness.tools.spill import SpillStore

    class _Root:
        def __init__(self, path):
            self.path = str(path)
            self.label = "scratch"
            self.writable = True

    big = "x" * 130_000  # one result over the whole turn budget
    registry = ToolRegistry()

    def dump() -> str:
        return big

    dump.__name__ = "dump"
    registry.register(dump, metadata=ToolMetadata())
    engine = TurnEngine(provider=ScriptedProvider([_tool_turn("dump", {}), _text_turn("done")]), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5", spill=SpillStore.under([_Root(tmp_path)]))
    _collect(engine, "dump it")
    folded = [m for m in engine.messages if m.get("role") == "tool"][0]["content"]
    assert len(folded) < 10_000 and "the complete output is at" in folded  # folded, not 130k
    # The full text really is on disk behind the locator. The model-facing text of an
    # unrendered result is its JSON, so the spilled copy is that JSON, quotes included.
    locator = folded.split("the complete output is at ")[1].split(" ")[0]
    assert Path(locator).read_text(encoding="utf-8").rstrip("\n") == json.dumps(big)


def test_the_estimators_bias_prices_the_pressure_signal(tmp_path):
    """The chars/4 estimator mis-prices JSON-heavy content; once the provider has priced
    one request, the since-request delta carries the measured bias instead of guessing 1.0."""
    engine = _engine(tmp_path, [_text_turn("hi")])[0]
    engine.messages.append({"role": "user", "content": "hello " * 100})
    # The provider says the request is 1.5x what chars/4 guesses for it.
    engine._record_context_measure(int(1.5 * engine._view_tokens()))
    assert engine._estimate_bias is not None and 1.4 < engine._estimate_bias < 1.6
    engine.messages.append({"role": "assistant", "content": "x" * 400})
    plain = engine._last_context_tokens + compaction.estimate_tokens(engine.messages[engine._context_measured_at:])
    assert engine._context_signal() > plain  # the delta is priced at the measured bias, not 1.0


class _Refusal(Exception):
    status_code = 400


def test_a_model_that_refuses_a_recording_is_not_sent_it_again(tmp_path):
    """The picker lists what a model takes and the sender acts on it. When a route still answers 400 to
    the recording, it stays in the history and would fail every later turn: the engine says so once,
    then names the file instead of sending it."""

    class Refusing(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=True, pdf=True, audio=True, video=True)

        def media_carry(self, model, mime):
            return True

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if any(isinstance(p, dict) and p.get("type") == "file" for m in messages if isinstance(m.get("content"), list) for p in m["content"]):
                raise _Refusal("Error code: 400 - {'error': {'message': \"Invalid value: 'input_audio' is not supported\"}}")
            return AssistantTurn(text="ok", finish_reason="stop")

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = Refusing([])
    engine.messages.append({"role": "user", "content": [{"type": "text", "text": "listen"}, _audio_file_part()]})
    error = next(event for event in _collect(engine, "what does it say?") if event.type == EventType.ERROR)
    assert "did not accept the recording" in error.data["error"] and "named instead of sent" in error.data["error"]
    assert EventType.ERROR not in _types(_collect(engine, "try again"))  # the recording is named now, so the request goes through
    assert any("call.mp3" in str(m.get("content")) for m in engine._outbound_messages())
