"""A cut adds a part to the block and leaves the earlier ones as they were written.

Scripted providers only. What the parts are worth to a model is measured by the probe
`benchmarks/tasks-probe/details-across-cuts`; these pin what the harness does with them.
"""

from __future__ import annotations

from summary_fixtures import valid_summary

from harness.context import compaction
from harness.context.compaction import (
    CompactionState,
    build_state,
    estimate_tokens,
    merge_parts,
    merge_pick,
    part,
    parts_of,
    render_parts,
)
from harness.llm.providers import AssistantTurn

from test_compaction import FakeSummarizer, assistant, convo, user
from test_context_fold import DONE, NOTE, _fold_call, collect, history, make_engine


def _ask(text="combined"):
    calls = []

    def ask(messages, tokens):
        calls.append(messages)
        return text

    return ask, calls


# -- the parts themselves -------------------------------------------------------------


def test_a_state_from_before_parts_is_one_part_and_is_sent_as_it_was():
    old = CompactionState.from_dict({"boundary_index": 3, "summary_text": "the gist", "working_state": ""})
    assert old.parts == [] and parts_of(old) == [part("the gist")]
    # Its block keeps the text it was saved with until the next cut renders the parts,
    # and names no part ends: offsets taken from a rendering it was never sent in would cut it wrongly.
    assert "\nthe gist" in compaction.compacted_block(old) and "### Part" not in compaction.compacted_block(old)
    assert compaction.block_segments(old) == []
    new = CompactionState(boundary_index=3, summary_text="", working_state="", parts=[part("a"), part("b", "note", 2)])
    again = CompactionState.from_dict(new.as_dict())
    assert again.parts == [part("a"), part("b", "note", 2)]


def test_merges_keep_the_shape_of_a_binary_counter():
    """Combining the least-rewritten pair: after n cuts no part has been rewritten more than
    about log2(n) times, where one running summary rewrote its oldest content n - 1 times."""
    parts: list[dict] = []
    for cut in range(21):
        parts.append(part(f"cut {cut} " + "x" * 400))
        parts, _ = merge_parts(parts, ask=_ask("y" * 400)[0], budget=estimate_tokens([{"role": "user", "content": "x" * 900}]), target_tokens=500)
    assert compaction.max_rewrites(parts) <= 5  # log2(21) = 4.4; the old chain: 20


def test_the_pair_picked_is_the_least_rewritten_and_the_older_on_a_tie():
    assert merge_pick([part("a", rewrites=2), part("b", rewrites=1), part("c", rewrites=0)]) == 1
    assert merge_pick([part("a"), part("b"), part("c")]) == 0
    assert merge_pick([part("a")]) is None


def test_a_merge_says_which_is_newer_and_a_failed_one_changes_nothing():
    ask, calls = _ask("together")
    parts = [part("older " * 300), part("newer " * 300, "note")]
    out, merged = merge_parts(parts, ask=ask, budget=10, target_tokens=500)
    assert merged == 1 and out == [part("together", "summary", 1)]
    asked = calls[0][1]["content"]
    assert asked.index("<older-part>") < asked.index("<newer-part>") and "the newer part" in calls[0][0]["content"]

    def broken(messages, tokens):
        raise RuntimeError("provider down")

    same, merged = merge_parts(parts, ask=broken, budget=10, target_tokens=500)
    assert merged == 0 and same == parts


# -- the summarizer path ----------------------------------------------------------------


def _grow(msgs, start, end, bulk=2000):
    for i in range(start, end):
        msgs.append(user(f"request {i}"))
        msgs.append(assistant(f"answer {i} " + "x" * bulk))


class _Merging(FakeSummarizer):
    """Summaries as scripted; a merge answers with a combined part of the length it was asked."""

    def complete(self, *, model, messages, tools=None, **settings):
        if messages[0]["content"] == compaction.MERGE_INSTRUCTION:
            self.calls.append({"model": model, "messages": messages, "tools": tools, **settings})
            return AssistantTurn(text="combined " + "c" * 3_000, finish_reason="stop")
        return super().complete(model=model, messages=messages, tools=tools, **settings)


def test_parts_past_their_budget_are_combined_in_a_call_of_their_own():
    msgs = convo(turns=4, bulk=20_000)
    fake = _Merging(text=valid_summary("first " + "a" * 600))
    keep = lambda: estimate_tokens(msgs[-4:]) + 10  # noqa: E731
    state = build_state(msgs, provider=fake, model="m", keep_tokens=keep(), trigger=40_000)
    merged = []
    for stretch in range(1, 4):
        _grow(msgs, 4 * stretch, 4 * stretch + 4, bulk=20_000)
        fake.text = valid_summary(f"stretch {stretch} " + "b" * 600)
        state = build_state(msgs, provider=fake, model="m", keep_tokens=keep(), prior=state, trigger=40_000)
        merged.append(state.merged)

    merges = [c for c in fake.calls if c["messages"][0]["content"] == compaction.MERGE_INSTRUCTION]
    assert merges and sum(merged) == len(merges) >= 1  # `merged` is what each cut combined
    assert compaction.parts_tokens(state.parts) <= compaction.parts_budget(40_000)
    assert compaction.max_rewrites(state.parts) >= 1
    # A merge is a request on the two parts' text alone: no tools, no conversation.
    assert all(c["tools"] is None and len(c["messages"]) == 2 for c in merges)


def test_the_working_state_covers_everything_cut_not_only_the_last_span():
    msgs = [{"role": "system", "content": "s"}, user("go")]
    msgs.append(assistant("writing", tool_calls=[("write_file", {"path": "first.py"})]))
    msgs.append({"role": "tool", "tool_call_id": msgs[-1]["tool_calls"][0]["id"], "content": "ok", "ts": 1.0})
    _grow(msgs, 0, 3)
    fake = FakeSummarizer()
    first = build_state(msgs, provider=fake, model="m", keep_tokens=estimate_tokens(msgs[-2:]) + 10)
    _grow(msgs, 3, 6)
    second = build_state(msgs, provider=fake, model="m", keep_tokens=estimate_tokens(msgs[-2:]) + 10, prior=first)
    assert "first.py" in first.working_state and "first.py" in second.working_state


# -- the fold path ----------------------------------------------------------------------


def test_a_fold_past_the_budget_combines_two_parts_and_says_so_in_the_log(tmp_path):
    merged_text = "combined: `fetch_record` renamed in t1/store.py; 7 passed."
    turns = [
        _fold_call(),
        AssistantTurn(text=merged_text, finish_reason="stop"),  # the merge call
        DONE,
    ]
    engine, provider = make_engine(tmp_path, turns, messages=history(steps=24), window=40_000)
    big = "earlier work, " * 700
    engine.compaction_state = CompactionState(
        boundary_index=4, summary_text=render_parts([part(big), part(big, "note")]),
        working_state="", parts=[part(big), part(big, "note")],
    )
    collect(engine, "and now the changelog")

    state = engine.compaction_state
    assert state.merged == 1 and len(state.parts) == 2
    assert state.parts[0] == part(merged_text, "summary", 1)
    assert NOTE in state.parts[1]["text"] and state.parts[1]["author"] == "note"
    (row,) = [e for e in engine.log.read() if e.get("type") == "compaction/summary"]
    assert row["parts"] == 2 and row["merged"] == 1 and row["rewrites"] == 1
    assert row["parts_budget"] == compaction.parts_budget(engine._history_trigger_tokens())
    merge_request = provider.requests[1]
    assert merge_request[0]["content"] == compaction.MERGE_INSTRUCTION


def test_a_fold_inside_the_budget_makes_no_call(tmp_path):
    engine, provider = make_engine(tmp_path, [_fold_call(), DONE], messages=history())
    collect(engine, "and now the changelog")
    assert len(provider.requests) == 2  # the fold's step and the answer, nothing between
    assert engine.compaction_state.merged == 0 and len(engine.compaction_state.parts) == 1


def test_finished_work_is_kept_as_its_outcome_by_a_merge_and_a_summary():
    """A merge dropped what "the newer part says was finished and is no longer needed": in the long
    session that was task one's "22 passed", the next thing the person asked. Replayed on those two
    parts the old wording kept it 0 of 6 times, this one 6 of 6 (2026-10-23)."""
    from harness.context.compaction.summarizer import SUMMARY_SYSTEM_PROMPT

    assert "was finished and is no longer needed" not in compaction.MERGE_INSTRUCTION
    for text in (compaction.MERGE_INSTRUCTION, SUMMARY_SYSTEM_PROMPT):
        assert "Finished work" in text and "result line" in text
