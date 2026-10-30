"""A cut leaves the earlier parts of the block where the provider's cache has them.

What is pinned: the text before a block's last part does not change when the next cut adds one,
and the Anthropic request marks the end of the parts as a breakpoint, where the request before
the cut wrote them. Whether a real route then reads them from cache is for the store to say
(prediction `a-cut-keeps-the-earlier-parts-cached`).
"""

from __future__ import annotations

from harness.context import compaction
from harness.context.compaction import CompactionState, apply_to_outbound, compacted_block, part, render_parts
from harness.llm.providers.base import CACHE_SEGMENTS
from harness.llm.providers.chat.request import without_foreign_sidecars
from harness.llm.providers.claude import AnthropicProvider
from harness.llm.providers.claude.convert import CACHE_POINT

from test_compaction import assistant, user

NOTE_TEXT = "Renamed `fetch_record` to `load_record` in store.py; 22 passed. Left: the CLI flag. " * 3


def _messages(turns):
    messages = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(turns):
        messages += [user(f"request {i}"), assistant(f"answer {i} " + "x" * 3000)]
    return messages


def _fold(messages, prior=None, note=NOTE_TEXT):
    return compaction.build_fold_state(messages, note=note, keep_tokens=200, prior=prior, trigger=400_000)


def _sent(messages):
    return AnthropicProvider(client=object())._request_kwargs(
        model="claude-sonnet-5-5", messages=messages, tools=None, settings={}
    )


def _marks(sent):
    marks = 1 if any("cache_control" in block for block in sent.get("system") or []) else 0
    return marks + sum(
        1 for message in sent["messages"] for block in message["content"] if "cache_control" in block
    )


def test_a_rendering_of_n_parts_is_a_byte_prefix_of_the_rendering_with_one_more():
    parts = [part(f"part {i} " + "y" * 80, "note") for i in range(1, 6)]
    for n in range(1, 5):
        assert render_parts(parts[: n + 1]).startswith(render_parts(parts[:n]))
    # and the ends it names are where each part's text stops
    rendered = render_parts(parts)
    assert [rendered[:end].endswith(p["text"]) for p, end in zip(parts, compaction.part_ends(parts))] == [True] * 5


def test_a_fold_leaves_the_block_unchanged_up_to_the_end_of_the_earlier_parts():
    messages = _messages(4)
    first = _fold(messages)
    messages += [user("request 4"), assistant("answer 4 " + "x" * 3000), user("request 5"), assistant("answer 5 " + "x" * 3000)]
    second = _fold(messages, prior=first, note="Second stretch: the CLI flag is `--record`, added and checked. " * 3)
    before, after = apply_to_outbound(messages, first)[1], apply_to_outbound(messages, second)[1]
    end = before[CACHE_SEGMENTS][-1]
    assert after["content"][:end] == before["content"][:end]
    assert after[CACHE_SEGMENTS][0] == before[CACHE_SEGMENTS][0] and len(after[CACHE_SEGMENTS]) == 2


def test_the_opening_lines_are_the_same_for_one_part_and_for_many():
    one = CompactionState(boundary_index=2, summary_text=render_parts([part("a" * 50)]), working_state="", parts=[part("a" * 50)])
    two_parts = [part("a" * 50), part("b" * 50, "note")]
    two = CompactionState(boundary_index=2, summary_text=render_parts(two_parts), working_state="", parts=two_parts)
    assert compacted_block(one).startswith(compaction.BLOCK_OPENING)
    assert compacted_block(two).startswith(compaction.BLOCK_OPENING + render_parts(two_parts[:1]))


def test_the_anthropic_request_marks_the_end_of_the_parts_and_sends_the_block_whole():
    messages = _messages(6)
    state = _fold(messages, prior=_fold(messages[:7]))
    outbound = apply_to_outbound(messages, state)
    sent = _sent(outbound)
    blocks = sent["messages"][0]["content"]
    ends = outbound[1][CACHE_SEGMENTS]
    # one text block per part, then the rest of the block, then the tail's first message
    texts = [block["text"] for block in blocks[: len(ends) + 1]]
    assert "".join(texts) == compacted_block(state)
    assert ["cache_control" in block for block in blocks[: len(ends) + 1]] == [False] * (len(ends) - 1) + [True, False]
    assert _marks(sent) == 3  # system, the parts, the last message: under the API's four
    assert CACHE_POINT not in repr(sent) and CACHE_SEGMENTS not in repr(sent)


def test_offsets_that_do_not_fit_the_text_leave_the_block_whole_and_unmarked():
    for ends in ([5, 3], [0], [10_000], ["7"], []):
        block = {"role": "user", "content": "<compacted-history>\n" + "z" * 200, CACHE_SEGMENTS: ends}
        sent = _sent([{"role": "system", "content": "s"}, block, {"role": "assistant", "content": "ok"}, user("next")])
        assert len(sent["messages"][0]["content"]) == 1 and _marks(sent) == 2


def test_a_state_saved_in_the_old_rendering_names_no_part_ends():
    parts = [part("old one"), part("old two", "note")]
    saved = CompactionState(
        boundary_index=2, summary_text="### Part 1 of 2 (oldest)\nold one\n\n### Part 2 of 2 (newest)\nold two",
        working_state="", parts=parts,
    )
    assert compaction.block_segments(saved) == []
    assert CACHE_SEGMENTS not in apply_to_outbound(_messages(3), saved)[1]


def test_a_compatible_wire_is_never_sent_the_part_ends():
    messages = _messages(4)
    outbound = apply_to_outbound(messages, _fold(messages))
    assert CACHE_SEGMENTS in outbound[1]
    assert all(CACHE_SEGMENTS not in message for message in without_foreign_sidecars(outbound))
