"""The aggregate output budget bounds one STEP, and never shrinks a self-bounded read.

Found by the 2026-09-24 harness audit on the real session store: once a turn's tool output
passed 120k characters, every later result — `read_file` included — came back as a 4k
stub, for the rest of the USER turn. The model re-read the file (24 times in the corpus)
and got the same stub back (13 of those were shrunk again): a long coding turn lost the
ability to read code. The mechanism was ported from a turn-budget rule, which
applies to ONE batch of results (`tool_executor.py`, `messages[-num_tools:]`) and pins
`read_file` at an infinite threshold precisely to break that persist→read loop — both
halves were lost in the port. These tests drive the real loop and fail on the old shape.
"""

from __future__ import annotations

import asyncio

from harness.loop import STEP_OUTPUT_BUDGET, TurnEngine
from harness.loop.limits import SHRUNK_RESULT_BUDGET as _SHRUNK_RESULT_BUDGET
from harness.tools.retention import RESULT_CEILING_CHARS
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.files import file_tools

#: Big enough that three of them cross the step budget, small enough that `read_file`'s
#: own 40k window returns each one whole.
_FILE_CHARS = 36_000


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="done", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _calls(*pairs):
    return AssistantTurn(
        tool_calls=[ToolCall(id=cid, name=name, arguments=args) for cid, name, args in pairs],
        finish_reason="tool_calls",
    )


def blob(size: int) -> str:
    """A low-risk tool with no contract: the shape of an MCP/connector result."""
    return "b" * size


def _engine(tmp_path, turns) -> TurnEngine:
    for index in range(5):
        # Many short lines, the shape of real source; every line is under the reader's
        # per-line clip, so a whole read is exactly the file.
        line = f"line {index} " + "x" * 70 + "\n"
        (tmp_path / f"f{index}.py").write_text(line * (_FILE_CHARS // len(line)), encoding="utf-8")
    registry = ToolRegistry()
    registry.register_all(file_tools(str(tmp_path)))
    registry.register(blob)
    return TurnEngine(
        provider=ScriptedProvider(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, auto_allow_tools={"blob"}),
        model="x",
    )


def _run(engine: TurnEngine) -> None:
    async def go():
        return [ev async for ev in engine.run("read them")]

    asyncio.run(go())


def _result(engine: TurnEngine, call_id: str) -> str:
    for message in engine.messages:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return str(message.get("content") or "")
    raise AssertionError(f"no result for {call_id}")


def test_a_read_past_the_budget_in_the_same_step_is_returned_whole(tmp_path):
    reads = [(f"r{i}", "read_file", {"path": f"f{i}.py"}) for i in range(5)]
    engine = _engine(tmp_path, [_calls(*reads)])
    _run(engine)
    # 5 × ~36k is well past the step budget; every read still carries its whole file.
    assert 5 * _FILE_CHARS > STEP_OUTPUT_BUDGET
    for call_id, _, args in reads:
        text = _result(engine, call_id)
        assert "omitted from the middle" not in text
        assert len(text) > _FILE_CHARS * 0.95, (call_id, len(text))


def test_the_budget_still_bites_inside_one_step(tmp_path):
    batch = [(f"b{i}", "blob", {"size": 45_000}) for i in range(3)]
    engine = _engine(tmp_path, [_calls(*batch)])
    _run(engine)
    assert len(_result(engine, "b1")) >= 45_000
    # The third result of the same batch pushed it past the budget.
    assert len(_result(engine, "b2")) <= _SHRUNK_RESULT_BUDGET


def test_the_budget_starts_over_on_the_next_step(tmp_path):
    # Step one keeps 115k — just under the budget, nothing shrunk.
    first = [("b0", "blob", {"size": 45_000}), ("b1", "blob", {"size": 45_000}),
             ("b2", "blob", {"size": 25_000})]
    second = [("late", "blob", {"size": 10_000})]
    engine = _engine(tmp_path, [_calls(*first), _calls(*second)])
    _run(engine)
    assert len(_result(engine, "b2")) >= 25_000
    # A later step is a new batch: its result is not shrunk for what step one spent.
    # (Summed over the turn it would be 125k, which the old per-turn budget shrank.)
    assert len(_result(engine, "late")) >= 10_000


def test_a_self_bounded_read_still_answers_to_the_per_result_ceiling(tmp_path):
    """The exemption is from the AGGREGATE stub only; the outer 50k ceiling bounds every
    result, whatever the contract says (a syntax/LSP note can be appended to a read)."""
    engine = _engine(tmp_path, [])
    registry = engine.registry
    rendered = "r" * (RESULT_CEILING_CHARS * 2)
    call = ToolCall(id="c", name="read_file", arguments={"path": "f0.py"})
    assert registry.contract("read_file").self_bounded is True
    engine._step_output_chars = STEP_OUTPUT_BUDGET * 10
    kept, _ = engine._enforce_turn_budget(call, rendered, {"path": "f0.py"})
    assert _SHRUNK_RESULT_BUDGET < len(kept) <= RESULT_CEILING_CHARS
