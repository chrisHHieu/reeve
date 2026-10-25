"""The model folds its own context: a compaction it authors, through the same
transaction, with the harness's guarantees still made by code.

Scripted providers only. What a model does with the tool is measured elsewhere
(`benchmarks/ablation.py --enable model_fold`); these pin what the HARNESS does when one calls it.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio

import pytest

from harness.context import compaction
from harness.context.compaction.fold import ACTIVE_REQUEST_CLIP, NOTE_HEADING, FoldRefused, build_fold_state
from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.session.invariants import _empty_index, _index_events, audit_messages
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.context_fold import FOLD_CONTEXT, context_fold_tools

NOTE = (
    "Renamed fetch_record to load_record in t1/store.py (line 14) and its two callers in "
    "t1/api.py; pytest in t1 passes (7 passed). Left: the changelog entry."
)


def history(steps: int = 12, bulk: int = 2_000, request: str = "rename fetch_record everywhere") -> list[dict]:
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": request, "ts": 1.0},
    ]
    for i in range(steps):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
        messages.append({"role": "assistant", "content": f"reading {i}", "tool_calls": [call], "ts": 1.0})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "line of code\n" * (bulk // 13), "ts": 1.0})
    return messages


# -- the pure half -----------------------------------------------------------------------


def test_a_fold_replaces_the_older_span_with_the_note_and_keeps_a_tail():
    messages = history()
    state = build_fold_state(messages, note=NOTE, keep_tokens=1_500)
    view = compaction.apply_to_outbound(messages, state)

    assert 2 < state.boundary_index < len(messages)
    assert messages[state.boundary_index]["role"] == "assistant"  # never opens on a tool result
    assert view[-1] is messages[-1]  # the tail is the same messages, untouched
    block = view[1]["content"]
    assert NOTE_HEADING in block and NOTE in block
    assert compaction.estimate_tokens(view) < compaction.estimate_tokens(messages) / 2
    assert len(messages) == len(history())  # canonical history is not the thing that changed


def test_the_request_being_worked_on_survives_whole_and_older_ones_are_clipped():
    """A note can be thin. The request cannot be what it costs: the fold reaches into the
    current turn, so the user message it folds is the task, kept nearly whole by code."""
    long_request = "Refactor the store. " + "Detail of the requirement. " * 60
    assert 600 < len(long_request) < ACTIVE_REQUEST_CLIP
    state = build_fold_state(history(request=long_request), note=NOTE, keep_tokens=1_500)
    assert state.user_messages == [" ".join(long_request.split())]

    # The same message one turn back is history, clipped like every other old user message.
    older = history(request=long_request)
    older.insert(14, {"role": "user", "content": "now the changelog", "ts": 2.0})
    state = build_fold_state(older, note=NOTE, keep_tokens=200)
    kept = state.user_messages[0]
    assert len(kept) <= 600 and "characters cut…]" in kept
    # Both ends: what a long message opens with and what it closes with.
    assert kept.startswith("Refactor the store.") and kept.endswith("Detail of the requirement.")


def test_a_note_too_short_to_be_a_memory_is_refused_and_says_what_belongs_in_one():
    with pytest.raises(FoldRefused, match="what is done and verified"):
        build_fold_state(history(), note="done, moving on", keep_tokens=1_500)


def test_nothing_older_than_the_tail_is_nothing_to_fold():
    short = history(steps=1, bulk=100)
    with pytest.raises(FoldRefused, match="nothing to fold"):
        build_fold_state(short, note=NOTE, keep_tokens=50_000)


def test_a_fold_that_would_not_shrink_the_context_is_refused():
    small = history(steps=3, bulk=40)
    with pytest.raises(FoldRefused, match="would not shrink"):
        build_fold_state(small, note="n" * 4_000, keep_tokens=0)


def test_a_second_fold_adds_a_part_and_the_first_stays_as_it_was_written():
    """A fold no longer offers to replace the earlier notes; each is a part of the
    block, kept word for word, and the harness alone combines parts past their budget."""
    messages = history(steps=6)
    first = build_fold_state(messages, note=NOTE, keep_tokens=1_500)
    more = messages + history(steps=6)[2:]
    second_note = (
        "Second stretch: wrote the changelog entry under 2.4.0 in t1/CHANGELOG.md and re-ran "
        "pytest in t1, which still passes with 7 tests. Nothing is left in t1."
    )

    kept = build_fold_state(more, note=second_note, keep_tokens=1_500, prior=first)
    assert [p["text"] for p in kept.parts] == [f"{NOTE_HEADING}\n{NOTE}", f"{NOTE_HEADING}\n{second_note}"]
    assert [p["author"] for p in kept.parts] == ["note", "note"]
    assert kept.summary_text.index(NOTE) < kept.summary_text.index(second_note)
    assert kept.boundary_index > first.boundary_index
    assert kept.user_messages == ["rename fetch_record everywhere"]
    with pytest.raises(TypeError):
        build_fold_state(more, note=second_note, keep_tokens=1_500, prior=first, replace_notes=True)


def test_a_secret_pasted_into_a_note_does_not_ride_every_later_request():
    leaky = NOTE + " The key is sk-ant-api03-" + "a" * 40 + " for the staging bucket."
    state = build_fold_state(history(), note=leaky, keep_tokens=1_500)
    assert "sk-ant-api03-" + "a" * 40 not in state.summary_text


# -- the engine half ---------------------------------------------------------------------


class Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(list(messages))
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def make_engine(tmp_path, turns, *, messages=None, window=200_000, fold=True):
    provider = Scripted(turns)
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {
        "context_window": window, "buffer_tokens": 0, "keep_tokens": 1_500, "fold_keep_tokens": 1_500,
    }
    if fold:
        engine.enable_model_fold()
    return engine, provider


def collect(engine, text="continue"):
    async def _run():
        return [e async for e in engine.run(text)]

    return asyncio.run(_run())


def _events(engine, kind):
    return [e for e in engine.log.read() if e.get("type") == kind]


def _fold_call(note=NOTE, **extra):
    return AssistantTurn(
        text="folding", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="f1", name=FOLD_CONTEXT, arguments={"note": note, **extra})],
    )


def test_a_folded_context_is_what_the_next_request_carries(tmp_path):
    engine, provider = make_engine(
        tmp_path, [_fold_call(), AssistantTurn(text="done", finish_reason="stop")], messages=history()
    )
    canonical = len(engine.messages)
    events = collect(engine, "and now the changelog")

    before, after = provider.requests
    assert len(after) < len(before) / 2
    block = after[1]["content"]
    assert "<compacted-history>" in block and NOTE in block
    assert "rename fetch_record everywhere" in block  # the earlier request, verbatim, by code
    # The step that folded is in the tail: its call and the result that answers it.
    assert any(m.get("role") == "tool" and "Folded" in str(m.get("content")) for m in after)
    # Nothing was removed from the transcript: the fold is a projection.
    assert len(engine.messages) > canonical
    assert any(e.type == EventType.COMPACTED for e in events)


def test_a_fold_is_told_by_its_own_row_in_numbers(tmp_path):
    """The GUI draws a fold as one step of the turn from the result's card; the
    compaction divider used to tell it a second time and cut the turn in two."""
    engine, _ = make_engine(
        tmp_path, [_fold_call(), AssistantTurn(text="done", finish_reason="stop")], messages=history()
    )
    events = collect(engine)

    (finished,) = [e for e in events if e.type == EventType.TOOL_FINISHED]
    shown = finished.data["presentation"]
    card = shown["card"]
    assert card["card"] == "fold" and "note" not in card
    (compacted,) = [e for e in events if e.type == EventType.COMPACTED]
    assert card["saved_tokens"] == compacted.data["saved_tokens"] > 0
    assert card["before_tokens"] - card["after_tokens"] == card["saved_tokens"]
    assert card["folded_messages"] > 0 and card["kept_messages"] > 0
    assert shown["summary"] == f"Folded {card['folded_messages']} earlier messages into its own note"


def test_a_refused_fold_has_no_numbers_to_show(tmp_path):
    engine, _ = make_engine(
        tmp_path, [_fold_call(note="short"), AssistantTurn(text="done", finish_reason="stop")], messages=history()
    )
    events = collect(engine)

    (finished,) = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert finished.data["presentation"]["card"]["card"] == "generic"


def test_a_fold_is_the_same_durable_transaction_a_compaction_is(tmp_path):
    engine, _ = make_engine(
        tmp_path, [_fold_call(), AssistantTurn(text="done", finish_reason="stop")], messages=history()
    )
    collect(engine)

    (start,) = _events(engine, "compaction/start")
    (summary,) = _events(engine, "compaction/summary")
    (end,) = _events(engine, "compaction/end")
    assert start["author"] == "model"
    assert summary["reason"] == end["reason"] == "folded"
    assert summary["saved_tokens"] > 0 and summary["boundary"] == engine.compaction_state.boundary_index
    assert engine.log.dangling_compactions() == 0
    # The person can see it happened and what was kept.
    notices = [m for m in engine.messages if m.get("role") == "notice" and m.get("kind") == "compacted"]
    assert len(notices) == 1 and NOTE in str(notices[0])


def test_a_refused_fold_changes_nothing_and_opens_no_transaction(tmp_path):
    engine, provider = make_engine(
        tmp_path,
        [_fold_call(note="ok"), AssistantTurn(text="done", finish_reason="stop")],
        messages=history(),
    )
    collect(engine)

    assert engine.compaction_state is None
    assert _events(engine, "compaction/start") == []
    answer = next(m for m in engine.messages if m.get("tool_call_id") == "f1")
    assert str(answer["content"]).startswith("Error: not folded")
    assert len(provider.requests[1]) > len(provider.requests[0])  # the view only grew


def test_a_fold_that_would_free_too_little_is_refused(tmp_path):
    """Folding rewrites the prompt after the block, so the provider re-prices it. A fold that
    frees a few percent of the view pays that for nothing — even when the block it writes IS
    smaller than the span it replaces, which is the case here: a short conversation under a
    large system prompt."""
    messages = history(steps=6, bulk=1_500)
    messages[0] = {"role": "system", "content": "rules. " * 20_000}
    engine, _ = make_engine(tmp_path, [], messages=messages)
    engine.compaction_settings = lambda: {"context_window": 400_000, "buffer_tokens": 0, "fold_keep_tokens": 200}
    # The fold itself is a real one: the pure half accepts it.
    build_fold_state(messages, note=NOTE, keep_tokens=200)

    result = engine.fold_context(NOTE)
    assert "would free" in result["error"] and "Keep working" in result["error"]
    assert engine.compaction_state is None and _events(engine, "compaction/start") == []


def test_a_fold_keeps_its_own_short_tail_whatever_the_compaction_keeps(tmp_path):
    """Under the product's capped window the compaction keeps up to 40k tokens verbatim. A
    fold with that tail keeps the very steps the model just called finished (third pilot,
    2026-10-04)."""
    engine, _ = make_engine(tmp_path, [], messages=history(steps=30))
    engine.compaction_settings = lambda: {"context_window": 200_000, "buffer_tokens": 0, "keep_tokens": 40_000}
    assert engine._fold_keep_tokens() == 8_000
    result = engine.fold_context(NOTE)
    assert "error" not in result
    kept = engine.messages[engine.compaction_state.boundary_index:]
    assert compaction.estimate_tokens(kept) <= 8_000 < compaction.estimate_tokens(engine.messages) / 2


def test_folding_is_off_unless_the_session_turned_it_on(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(), fold=False)
    assert engine.registry.get(FOLD_CONTEXT) is None
    assert engine.fold_context(NOTE) == {"error": "folding is not enabled in this session"}
    assert engine.compaction_state is None


def test_a_staged_undo_holds_a_fold_back(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history())
    engine.revert_state = {"boundary": 6}
    assert "undo" in engine.fold_context(NOTE)["error"]
    assert engine.compaction_state is None


def test_the_tool_does_not_travel_to_a_child_registry(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history())
    child = engine.registry.filtered(session_bound=False)
    assert engine.registry.get(FOLD_CONTEXT) is not None and child.get(FOLD_CONTEXT) is None


# -- the hint ----------------------------------------------------------------------------
#
# WHEN it is given is the design (second pilot, 2026-10-04): a hint by size alone landed
# between "read twenty files" and "edit them", was ignored, and was spent by the time the
# work was done. It is given where work has just ended.


def _step(i, name="noop", arguments=None):
    return AssistantTurn(
        text="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id=f"r{i}", name=name, arguments=arguments or {})],
    )


def _with_tools(engine):
    from harness.tools.todo import TodoList, todo_tools

    def noop() -> str:
        """Do nothing."""
        return "ok"

    engine.registry.register(noop, metadata=ToolMetadata())
    engine.todo = TodoList()
    engine.registry.register_all(todo_tools(engine.todo))


def _todos(*statuses):
    return {"todos": [{"content": f"item {i}", "status": s} for i, s in enumerate(statuses)]}


def _hints(engine):
    return [m for m in engine.messages if m.get("role") == "user" and "fold_context" in str(m.get("content")) and "System note" in str(m.get("content"))]


def _fold_rows(engine):
    return [e for e in _events(engine, "context/budget") if e.get("hint") == "fold"]


DONE = AssistantTurn(text="done", finish_reason="stop")


def test_working_steps_get_no_hint_however_much_has_accumulated_short_of_late(tmp_path):
    """Size alone is not a moment. Three working steps over a context well past the
    threshold: nothing is said, because nothing has finished."""
    engine, _ = make_engine(tmp_path, [_step(1), _step(2), _step(3), DONE], messages=history(steps=16), window=12_000)
    _with_tools(engine)
    engine._stamp_fold_hint = lambda message: None  # the turn-start stamp is its own test
    collect(engine)
    assert engine._foldable_tokens(engine._context_signal()) >= engine._fold_hint_tokens()
    assert _hints(engine) == [] and _fold_rows(engine) == []


def test_finishing_an_item_is_the_moment_and_the_hint_is_logged(tmp_path):
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending")),
        _step(2),
        _step(3, "todo_write", _todos("done", "pending")),  # an item finished, another to start
        _step(4),
        DONE,
    ]
    engine, _ = make_engine(tmp_path, turns, messages=history(steps=16), window=12_000)
    _with_tools(engine)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)

    (hint,) = _hints(engine)
    assert "you just finished an item" in hint["content"]
    # The fold rides the step that starts the next item: no step of its own.
    assert "in the SAME step as the calls that start the next item" in hint["content"]
    # It follows the step that marked the item done, not the one that opened the list.
    position = engine.messages.index(hint)
    assert engine.messages[position - 1].get("tool_call_id") == "r3"
    (row,) = _fold_rows(engine)
    assert row["moment"] == "item" and row["foldable"] >= engine._fold_hint_tokens()
    assert row["used"] > row["foldable"]
    # Model-visible means logged: the audit accepts the hint because that row exists.
    index = _empty_index()
    _index_events(index, engine.log.read())
    assert not [v for v in audit_messages(engine.messages, index) if "fold_hint" in v]


def test_a_new_message_from_the_person_carries_the_hint_in_its_own_stamp(tmp_path):
    engine, provider = make_engine(tmp_path, [DONE], messages=history(steps=16), window=12_000)
    engine.messages.append({"role": "assistant", "content": "all renamed", "ts": 1.0})
    collect(engine, "now write the changelog")

    sent = provider.requests[0][-1]
    assert sent["role"] == "user" and sent["content"].startswith("now write the changelog")
    assert "<context-fold>" in sent["content"] and "from before this message" in sent["content"]
    assert "in the SAME step as your first calls for it" in sent["content"]
    # The transcript keeps the person's clean text; no extra message entered it.
    typed = [m for m in engine.messages if m.get("content") == "now write the changelog"]
    assert len(typed) == 1 and _hints(engine) == []
    (row,) = _fold_rows(engine)
    assert row["moment"] == "turn" and row["turn"] == 1


def test_a_declined_item_hint_is_not_repeated_until_the_stretch_has_grown_by_a_threshold(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(steps=16), window=12_000)
    step = engine._fold_hint_tokens()
    first = engine._fold_hint_due(engine._context_signal(), "item")
    assert first is not None and first >= step
    assert engine._fold_hint_due(engine._context_signal(), "item") is None  # same size: not again

    engine.messages.extend(history(steps=3)[2:])
    engine._last_context_tokens = None
    assert engine._fold_hint_due(engine._context_signal(), "item") is None  # grew, under a threshold

    engine.messages.extend(history(steps=16)[2:])
    assert engine._fold_hint_due(engine._context_signal(), "item") is not None


def test_an_item_hint_the_model_declined_does_not_cost_the_next_turn_its_hint(tmp_path):
    """Sixth pilot: the item was finished on the turn's last step, the model answered instead
    of folding, and the next message then came with no hint because the stretch "had not
    grown". The new message is the moment the model does fold at."""
    engine, provider = make_engine(tmp_path, [DONE], messages=history(steps=16), window=12_000)
    assert engine._fold_hint_due(engine._context_signal(), "item") is not None  # given, declined
    engine.messages.append({"role": "assistant", "content": "all renamed", "ts": 1.0})
    collect(engine, "now the next task")
    assert "<context-fold>" in provider.requests[0][-1]["content"]


def test_with_no_boundary_at_all_the_hint_still_comes_late(tmp_path):
    """A turn that never finishes an item must not be left to the trigger alone: at three
    thresholds of foldable context the hint is given anyway, worded for mid-work."""
    engine, _ = make_engine(tmp_path, [], messages=history(steps=16), window=12_000)
    _with_tools(engine)
    engine._maybe_fold_hint()
    assert _hints(engine) == []  # one threshold: not late yet

    engine.messages.extend(history(steps=40)[2:])
    engine._last_context_tokens = None
    engine._maybe_fold_hint()
    (hint,) = _hints(engine)
    assert "When the piece of work you are on is finished" in hint["content"]
    assert _fold_rows(engine)[0]["moment"] == "late"


def test_a_late_hint_does_not_spend_the_hint_a_finished_item_is_owed(tmp_path):
    """Fourth pilot: a burst of reads drew the by-size hint mid-work; two steps later the item
    was done and nothing was said, because the stretch's one hint was gone."""
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending")),  # mid-work, and already past "late"
        _step(2),
        _step(3, "todo_write", _todos("done", "pending")),         # …and then the item is finished
        DONE,
    ]
    engine, _ = make_engine(tmp_path, turns, messages=history(steps=16))
    engine.compaction_settings = lambda: {
        "context_window": 200_000, "buffer_tokens": 0, "fold_keep_tokens": 1_500, "fold_hint_tokens": 1_000,
    }
    _with_tools(engine)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)

    moments = [row["moment"] for row in _fold_rows(engine)]
    assert moments == ["late", "item"]
    assert "you just finished an item" in _hints(engine)[-1]["content"]


def test_finishing_the_last_open_item_is_not_a_moment_to_fold(tmp_path):
    """A fold is repaid by the steps after it, and after the last item comes the answer.
    Real session (14715fae-61f, 2026-10-04): asked there, the model spent a 50k-token step
    writing its test results into a note and then wrote them again as its report."""
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending")),
        _step(2),
        _step(3, "todo_write", _todos("done", "done")),  # both closed in one write: nothing is open
        DONE,
    ]
    engine, provider = make_engine(tmp_path, turns + [DONE], messages=history(steps=16), window=12_000)
    _with_tools(engine)
    stamp = engine._stamp_fold_hint
    engine._stamp_fold_hint = lambda message: None
    collect(engine)
    assert _hints(engine) == [] and _fold_rows(engine) == []

    # The boundary is not lost: the person's next message is asked about, as its own moment.
    engine._stamp_fold_hint = stamp
    collect(engine, "now the next task")
    assert "<context-fold>" in provider.requests[-1][-1]["content"]


def test_after_a_fold_the_next_stretch_can_be_hinted_again(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(steps=16), window=12_000)
    assert engine._fold_hint_due(engine._context_signal()) is not None
    assert "error" not in engine.fold_context(NOTE)
    engine._last_context_tokens = None
    assert engine._fold_hint_due(engine._context_signal()) is None  # just folded: nothing to fold

    engine.messages.extend(history(steps=16)[2:])  # the new stretch grows past the threshold
    engine._last_context_tokens = None
    assert engine._fold_hint_due(engine._context_signal()) is not None


def test_no_hint_without_the_tool(tmp_path):
    engine, provider = make_engine(tmp_path, [DONE], messages=history(steps=16), window=12_000, fold=False)
    collect(engine, "next")
    assert "<context-fold>" not in str(provider.requests[0][-1]["content"])
    assert _fold_rows(engine) == []


def test_the_hint_is_about_what_can_be_folded_not_about_the_whole_context(tmp_path):
    """About 18k tokens of every real request are the system prompt and the tool schemas. A
    rule on the total hinted on the second step of a small-window session, with nothing to
    fold (first pilot, 2026-10-04). Here the same conversation sits under a large fixed part:
    the provider's count is high, the foldable span is not, and no hint is due."""
    engine, _ = make_engine(tmp_path, [], messages=history(steps=3, bulk=400), window=40_000)
    engine._last_context_tokens = 30_000  # what a provider reports: mostly prompt and schemas
    engine._context_measured_at = len(engine.messages)
    assert engine._context_signal() == 30_000
    assert engine._foldable_tokens(30_000) == 0
    assert engine._fold_hint_due(30_000) is None

    # The same engine, after the conversation itself has grown past the threshold.
    engine.messages.extend(history(steps=120)[2:])
    assert engine._fold_hint_due(engine._context_signal()) is not None


def test_the_threshold_is_twelve_thousand_foldable_tokens_or_a_quarter_of_the_trigger(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(steps=2), window=400_000)
    assert engine._fold_hint_tokens() == 12_000
    small, _ = make_engine(tmp_path, [], messages=history(steps=2), window=40_000)
    assert small._fold_hint_tokens() == int(0.25 * small._history_trigger_tokens()) < 12_000
    # A session can set its own figure; the share of the trigger still caps it.
    engine.compaction_settings = lambda: {"context_window": 400_000, "buffer_tokens": 0, "fold_hint_tokens": 30_000}
    assert engine._fold_hint_tokens() == 30_000


# -- does this route's cache survive a fold ----------------------------------------------
#
# Measured: on deepseek-v4.1-flash the request after a fold read 15.8k tokens from
# cache; on mimo-v2.5 it read nothing in 9 of 14 and the session's full-price input doubled.
# The route cannot be known in advance, so the engine looks once and believes what it sees.


def _usage(fresh, cached):
    from harness.llm.providers.base import TokenUsage

    return TokenUsage(input=fresh, cache_read=cached, output=50)


def _fold_then(after_usage, *, before_cached=9_000):
    fold = _fold_call()
    fold.usage = _usage(400, before_cached)
    answer = AssistantTurn(text="done", finish_reason="stop", usage=after_usage)
    return [fold, answer]


def _fold_off_rows(engine):
    return [e for e in _events(engine, "context/budget") if e.get("hint") == "fold_off"]


def test_a_route_whose_cache_dies_with_a_fold_is_not_asked_to_fold_again(tmp_path):
    engine, _ = make_engine(tmp_path, _fold_then(_usage(16_000, 0)), messages=history(steps=16), window=12_000)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)

    assert engine.compaction_state is not None  # the fold itself was applied
    assert engine._fold_cache_lost == {engine.model}
    (row,) = _fold_off_rows(engine)
    assert row["model"] == engine.model and row["used"] == 16_000
    # However much piles up afterwards, this model is not asked again in this session.
    engine.messages.extend(history(steps=60)[2:])
    engine._last_context_tokens = None
    for moment in ("turn", "item", "late"):
        assert engine._fold_hint_due(engine._context_signal(), moment) is None


def test_a_route_whose_cache_survives_a_fold_keeps_being_asked(tmp_path):
    engine, _ = make_engine(tmp_path, _fold_then(_usage(2_800, 15_800)), messages=history(steps=16), window=12_000)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)

    assert engine._fold_cache_lost == set() and _fold_off_rows(engine) == []
    engine.messages.extend(history(steps=16)[2:])
    engine._last_context_tokens = None
    assert engine._fold_hint_due(engine._context_signal(), "turn") is not None


def test_a_cache_that_was_already_cold_says_nothing_about_the_fold(tmp_path):
    """A person back after an hour: the request that folds reads no cache either. An empty
    cache after it is then the idle hour's doing, and blaming the fold would switch the
    mechanism off on a route where it pays."""
    turns = _fold_then(_usage(16_000, 0), before_cached=0)
    engine, _ = make_engine(tmp_path, turns, messages=history(steps=16), window=12_000)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)

    assert engine.compaction_state is not None
    assert engine._fold_cache_lost == set() and _fold_off_rows(engine) == []


def test_only_the_request_right_after_a_fold_is_judged(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(steps=16), window=12_000)
    # No fold pending: a cold request is an ordinary cold request.
    engine._fold_cache_probe(_usage(9_000, 4_000))
    engine._fold_cache_probe(_usage(16_000, 0))
    assert engine._fold_cache_lost == set()


# -- on by default, for a session's own engine --------------------------------------------


def _built(tmp_path, monkeypatch, config_text="", **kwargs):
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    if config_text:
        (state / "config.toml").write_text(config_text, encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return build_engine(agent=reeve_agent(), workspace=workspace, provider=object(), **kwargs)


def test_a_sessions_own_engine_is_offered_the_fold_by_default(tmp_path, monkeypatch):
    engine = _built(tmp_path, monkeypatch)
    assert engine.model_fold is True and engine.registry.get(FOLD_CONTEXT) is not None
    names = [(s.get("function") or {}).get("name") for s in engine.registry.schemas()]
    assert FOLD_CONTEXT in names  # on the wire, not behind tool discovery


def test_the_config_switch_turns_it_off(tmp_path, monkeypatch):
    engine = _built(tmp_path, monkeypatch, "[compaction]\nmodel_fold = false\n")
    assert engine.model_fold is False and engine.registry.get(FOLD_CONTEXT) is None


def test_a_subagent_is_not_offered_it(tmp_path, monkeypatch):
    parent = _built(tmp_path, monkeypatch)
    seed = parent.registry.filtered(session_bound=False)
    child = _built(tmp_path, monkeypatch, base_registry=seed)
    assert child.model_fold is False and child.registry.get(FOLD_CONTEXT) is None


def test_the_ablation_can_take_it_away_again(tmp_path):
    engine, _ = make_engine(tmp_path, [], messages=history(steps=16), window=12_000)
    engine.disable_model_fold()
    assert engine.registry.get(FOLD_CONTEXT) is None
    assert engine._fold_hint_due(engine._context_signal(), "turn") is None
    assert engine.fold_context(NOTE) == {"error": "folding is not enabled in this session"}

    from benchmarks.harness_eval import ABLATABLE, OPT_IN

    assert "model_fold" in ABLATABLE and "model_fold" not in OPT_IN


def test_the_tool_says_what_it_did_in_a_sentence_and_refuses_in_one():
    (tool,) = context_fold_tools(lambda note: {
        "folded_messages": 20, "kept_messages": 4, "before_tokens": 41_000, "after_tokens": 6_200,
    })
    from harness.tools.context_fold import FOLD_CONTRACT

    said = FOLD_CONTRACT.render({"note": NOTE}, tool(note=NOTE))
    assert said.startswith("Folded 20 older messages") and "41,000" in said and "6,200" in said
    assert FOLD_CONTRACT.render({}, {"error": "nothing to fold yet"}) == "Error: not folded — nothing to fold yet"


# -- a fold must repay itself -------------------------------------------------
#
# Session 14715fae-61f, 2026-10-05, five folds: every one took a step of its own (13-17 s
# before any work), one was asked for with 15k foldable of 46k and needed four more steps to
# break even, two came when only the report was left, and the notes had grown to 18,467
# characters with nothing ever taking one away.


def _measured(engine, used, fixed):
    """An engine whose provider says `used`, `fixed` of it being what no fold removes."""
    engine._last_context_tokens = used
    engine._context_measured_at = len(engine.messages)
    engine._fold_floor = (0, fixed)


def test_a_fold_that_would_not_halve_the_request_is_not_asked_for(tmp_path):
    """The real turn: 46k in the request, 15k of it foldable. Asked for, it cost a 47k step to
    take 13k off each later one. The store's median turn has four steps."""
    engine, _ = make_engine(tmp_path, [], messages=history(steps=2), window=400_000)
    _measured(engine, 46_300, fixed=46_300 - 15_500 - 1_500)
    assert engine._foldable_tokens(46_300) == 15_500 >= engine._fold_hint_tokens()
    assert engine._fold_hint_due(46_300) is None
    assert engine._fold_hint_due(46_300, "item") is None

    # The next real turn: 45k of 81k. That one halves the request, and is asked for.
    _measured(engine, 81_000, fixed=81_000 - 45_200 - 1_500)
    assert engine._fold_hint_due(81_000) == 45_200

    # The share is a setting; 0 asks whenever the token threshold is met, as before.
    engine.compaction_settings = lambda: {"context_window": 400_000, "fold_keep_tokens": 1_500, "fold_hint_share": 0}
    _measured(engine, 46_300, fixed=46_300 - 15_500 - 1_500)
    assert engine._fold_hint_due(46_300) == 15_500


def test_an_item_in_progress_is_not_work_still_to_start(tmp_path):
    """The day after "another item still open" went in, the one open item was the report
    itself, already in progress, and the model folded before writing it again."""
    turns = [
        _step(1, "todo_write", _todos("in_progress", "pending", "pending")),
        _step(2),
        _step(3, "todo_write", _todos("done", "done", "in_progress")),  # only the report is left
        DONE,
    ]
    engine, _ = make_engine(tmp_path, turns, messages=history(steps=16), window=12_000)
    _with_tools(engine)
    engine._stamp_fold_hint = lambda message: None
    collect(engine)
    assert _hints(engine) == [] and _fold_rows(engine) == []


def test_a_fold_made_beside_working_calls_keeps_what_they_return(tmp_path):
    """The fold needs no step of its own. Called with the calls that start the next piece of
    work, it replaces the older span and the step's results are in the next request."""
    both = AssistantTurn(
        text="folding and starting", finish_reason="tool_calls",
        tool_calls=[
            ToolCall(id="f1", name=FOLD_CONTEXT, arguments={"note": NOTE}),
            ToolCall(id="w1", name="noop", arguments={}),
        ],
    )
    engine, provider = make_engine(tmp_path, [both, DONE], messages=history())
    _with_tools(engine)
    collect(engine, "and now the changelog")

    before, after = provider.requests
    assert len(after) < len(before) / 2 and "<compacted-history>" in after[1]["content"]
    results = {m["tool_call_id"]: m["content"] for m in after if m.get("role") == "tool"}
    assert set(results) == {"f1", "w1"} and results["w1"] == "ok" and "Folded" in results["f1"]
    # The call that asked for both is there too, so neither result is an answer to nothing.
    asked = next(m for m in after if m.get("role") == "assistant" and m.get("tool_calls"))
    assert [call["id"] for call in asked["tool_calls"]] == ["f1", "w1"]
    assert "It needs no step of its own" in context_fold_tools(lambda **_: {})[0].__tool_schema__["function"]["description"]


def test_a_note_past_its_own_limit_is_refused_and_large_notes_still_add():
    """The parts' budget replaced the notes' one, which the harness keeps:
    a note that only adds is never refused for what the earlier notes hold."""
    messages = history(steps=6, bulk=60_000)
    first = build_fold_state(messages, note="n" * 9_000, keep_tokens=1_500)
    more = messages + history(steps=6, bulk=60_000)[2:]
    second = build_fold_state(more, note="m" * 9_000, keep_tokens=1_500, prior=first)
    assert "n" * 9_000 in second.summary_text and "m" * 9_000 in second.summary_text

    with pytest.raises(FoldRefused, match="a note is held to"):
        build_fold_state(messages, note="x" * (compaction.NOTE_MAX_CHARS + 1), keep_tokens=1_500)


def test_no_hint_asks_the_model_to_rewrite_its_notes(tmp_path):
    big, provider = make_engine(tmp_path / "big", [DONE], messages=history(steps=40), window=400_000)
    big.compaction_state = compaction.CompactionState(
        boundary_index=6, summary_text="earlier note. " * 800, working_state=""
    )
    big.messages.append({"role": "assistant", "content": "all renamed", "ts": 1.0})
    collect(big, "now write the changelog")
    sent = provider.requests[0][-1]["content"]
    assert "<context-fold>" in sent and "replace" not in sent.lower()
    assert "notes" not in _fold_rows(big)[0]
    schema = context_fold_tools(lambda **_: {})[0].__tool_schema__["function"]
    assert list(schema["parameters"]["properties"]) == ["note"]
    assert "stay above it as they were written" in schema["description"]
