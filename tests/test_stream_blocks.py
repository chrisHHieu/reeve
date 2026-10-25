"""Stream blocks — the order a step happened in, recovered from a flat delta stream.

The property every test here defends: two buckets (one text, one reasoning) cannot say
whether the thinking came before the sentence or after it, and that ORDER is what a person
watching the screen is reading. A block index can say it. See harness/llm/providers/blocks.py.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall, ToolCallDelta
from harness.llm.providers.blocks import REASONING, TEXT, TOOL_CALL, BlockAssembler
from harness.tools import ToolRegistry


def push_all(*chunks: StreamChunk) -> list:
    blocks = BlockAssembler()
    out = []
    for chunk in chunks:
        out.extend(blocks.push(chunk))
    return out


def shape(deltas) -> list[tuple[int, str]]:
    """(block index, kind) per delta — the thing the surface folds on."""
    return [(d.index, d.kind) for d in deltas]


# -- opening a block ----------------------------------------------------------


def test_same_kind_in_a_row_stays_one_block():
    deltas = push_all(
        StreamChunk(text_delta="The "),
        StreamChunk(text_delta="answer "),
        StreamChunk(text_delta="is 4."),
    )
    assert shape(deltas) == [(0, TEXT), (0, TEXT), (0, TEXT)]


def test_a_change_of_kind_opens_the_next_block():
    deltas = push_all(
        StreamChunk(reasoning_delta="let me check "),
        StreamChunk(reasoning_delta="the file"),
        StreamChunk(text_delta="Reading it now."),
    )
    assert shape(deltas) == [(0, REASONING), (0, REASONING), (1, TEXT)]


def test_thinking_that_resumes_after_prose_is_a_NEW_block_not_the_old_one():
    """The whole point. Appending it to block 0 would replay the step as "all the thinking,
    then all the prose", which is not what happened and not what was on screen."""
    deltas = push_all(
        StreamChunk(reasoning_delta="first thought"),
        StreamChunk(text_delta="Let me start."),
        StreamChunk(reasoning_delta="second thought"),
        StreamChunk(text_delta="Done."),
    )
    assert shape(deltas) == [(0, REASONING), (1, TEXT), (2, REASONING), (3, TEXT)]


def test_one_chunk_may_carry_two_kinds_and_loses_neither():
    """The dataclass allows it, so a provider that batches a thought and a sentence into
    one wire frame must come out the other side with both."""
    deltas = push_all(StreamChunk(reasoning_delta="hmm", text_delta="Right."))
    assert shape(deltas) == [(0, REASONING), (1, TEXT)]
    assert [d.text for d in deltas] == ["hmm", "Right."]


def test_the_final_turn_is_not_a_delta():
    """It is the assembled answer, not a piece of one — the surface swaps the partial for
    it rather than appending it."""
    assert push_all(StreamChunk(turn=AssistantTurn(text="done"))) == []


# -- tool calls ---------------------------------------------------------------


def test_a_tool_call_is_its_own_block_and_keeps_its_fragments_together():
    deltas = push_all(
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="c1", name="read_file")),
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="c1", name="read_file", arguments_delta='{"path":')),
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="c1", name="read_file", arguments_delta='"q3.csv"}')),
    )
    assert shape(deltas) == [(0, TOOL_CALL)] * 3
    assert "".join(d.arguments_delta for d in deltas) == '{"path":"q3.csv"}'
    assert all(d.name == "read_file" for d in deltas)


def test_two_concurrent_calls_keep_their_arguments_apart():
    """Providers interleave fragments for parallel calls by their OWN index. Folding them
    onto one block would concatenate two JSON documents into one unparseable string."""
    deltas = push_all(
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="a", name="read_file", arguments_delta='{"path":')),
        StreamChunk(tool_call_delta=ToolCallDelta(index=1, id="b", name="run_shell", arguments_delta='{"cmd":')),
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="a", name="read_file", arguments_delta='"a.csv"}')),
        StreamChunk(tool_call_delta=ToolCallDelta(index=1, id="b", name="run_shell", arguments_delta='"ls"}')),
    )
    assert shape(deltas) == [(0, TOOL_CALL), (1, TOOL_CALL), (0, TOOL_CALL), (1, TOOL_CALL)]
    joined = {}
    for d in deltas:
        joined[d.index] = joined.get(d.index, "") + d.arguments_delta
    assert joined == {0: '{"path":"a.csv"}', 1: '{"cmd":"ls"}'}


def test_prose_after_a_tool_call_opens_a_fresh_text_block():
    deltas = push_all(
        StreamChunk(text_delta="Checking."),
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="c1", name="read_file")),
        StreamChunk(text_delta="Found it."),
    )
    assert shape(deltas) == [(0, TEXT), (1, TOOL_CALL), (2, TEXT)]


# -- the payload a surface reads ----------------------------------------------


def test_the_payload_carries_only_the_fields_its_kind_uses():
    """So a reader never has to work out whether an empty `name` on a text block means
    anything. It does not, because it is not there."""
    text, tool = push_all(
        StreamChunk(text_delta="hi"),
        StreamChunk(tool_call_delta=ToolCallDelta(index=0, id="c1", name="run_shell", arguments_delta="{}")),
    )
    assert text.as_dict() == {"index": 0, "block": TEXT, "text": "hi"}
    assert tool.as_dict() == {
        "index": 1,
        "block": TOOL_CALL,
        "call_id": "c1",
        "name": "run_shell",
        "arguments_delta": "{}",
    }


# -- through the engine -------------------------------------------------------


class _BlockProvider(ProviderClient):
    """Thinks, speaks, thinks again, then calls a tool — one step, four blocks."""

    def __init__(self) -> None:
        self.n = 0

    def complete(self, **kw):
        raise NotImplementedError

    def stream(self, *, model, messages, tools=None, **settings):
        self.n += 1
        if self.n > 1:
            yield StreamChunk(turn=AssistantTurn(text="all done", finish_reason="stop"))
            return
        yield StreamChunk(reasoning_delta="checking the file")
        yield StreamChunk(text_delta="Let me read it.")
        yield StreamChunk(reasoning_delta="path looks right")
        yield StreamChunk(
            tool_call_delta=ToolCallDelta(index=0, id="c1", name="probe", arguments_delta='{"q":')
        )
        yield StreamChunk(
            tool_call_delta=ToolCallDelta(index=0, id="c1", name="probe", arguments_delta='"x"}')
        )
        yield StreamChunk(
            turn=AssistantTurn(
                text="Let me read it.",
                reasoning="checking the filepath looks right",
                tool_calls=[ToolCall(id="c1", name="probe", arguments={"q": "x"})],
                finish_reason="tool_calls",
            )
        )

    def capabilities(self, model):
        return ModelCapabilities()


def probe(q: str = "") -> str:
    """A tool that returns something."""
    return "two rows"


def _run(tmp_path):
    registry = ToolRegistry()
    registry.register(probe)
    engine = TurnEngine(
        provider=_BlockProvider(),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS
        ),
        model="m",
    )

    async def go():
        return [e async for e in engine.run("read the file")]

    return asyncio.run(go())


def test_the_engine_streams_the_step_in_the_order_it_happened(tmp_path):
    streamed = [
        (e.type, e.data.get("index"))
        for e in _run(tmp_path)
        if e.type
        in (
            EventType.REASONING_DELTA,
            EventType.ASSISTANT_DELTA,
            EventType.TOOL_CALL_DELTA,
        )
    ]
    assert streamed == [
        (EventType.REASONING_DELTA, 0),
        (EventType.ASSISTANT_DELTA, 1),
        (EventType.REASONING_DELTA, 2),
        (EventType.TOOL_CALL_DELTA, 3),
        (EventType.TOOL_CALL_DELTA, 3),
    ]


def test_a_streamed_call_is_display_only_and_the_executed_one_comes_from_the_turn(tmp_path):
    """Nothing runs against a half-typed argument string: the call the engine executes is
    the parsed one on `AssistantTurn`, and the deltas only said it was being written."""
    events = _run(tmp_path)
    proposed = [e for e in events if e.type == EventType.TOOL_PROPOSED]
    assert [e.data["name"] for e in proposed] == ["probe"]
    assert proposed[0].data["arguments"] == {"q": "x"}


def test_block_indexes_restart_with_each_step(tmp_path):
    """A block index is a step's private numbering — the second step's first block is 0
    again, or a surface folding by index would append step two onto step one."""
    events = _run(tmp_path)
    ends = [i for i, e in enumerate(events) if e.type == EventType.ITERATION_END]
    second = events[ends[0] :] if ends else []
    indexes = [
        e.data.get("index")
        for e in second
        if e.type in (EventType.ASSISTANT_DELTA, EventType.REASONING_DELTA)
    ]
    assert indexes == [] or min(indexes) == 0
