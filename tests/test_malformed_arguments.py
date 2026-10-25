"""A model that cannot produce readable tool-call arguments must not burn the budget.

The provider hands back `{"_raw": "<unparseable string>"}` when the model's arguments
are not JSON — every tool then fails validation, and the arguments differ each time, so
the outcome-sensitive repeat guard never sees a loop. Three in a row
end the turn through the same checkpoint-on-cap path every other cap uses.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry

MALFORMED = {"_raw": '{"path": "x"'}


class _Scripted(ProviderClient):
    """Emits the batches it is given, one per complete(); text when the list runs out."""

    def __init__(self, batches, *, tail="done"):
        self._batches = list(batches)
        self._tail = tail
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if not self._batches:
            return AssistantTurn(text=self._tail, finish_reason="stop")
        calls = [
            ToolCall(id=f"c{self.calls}-{i}", name=name, arguments=arguments)
            for i, (name, arguments) in enumerate(self._batches.pop(0))
        ]
        return AssistantTurn(tool_calls=calls, finish_reason="tool_calls")

    def capabilities(self, model):
        return ModelCapabilities()


async def _approve(_request):
    return ApprovalOutcome.ONCE


def _engine(tmp_path, provider, *, max_iterations=8):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.mode = Mode.WORKSPACE_WRITE
    return TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=_approve,
        max_iterations=max_iterations,
    )


def _run(engine):
    async def go():
        return [event async for event in engine.run("do it")]

    return asyncio.run(go())


def _tool_contents(engine):
    return [m["content"] for m in engine.messages if m.get("role") == "tool"]


def test_three_consecutive_malformed_calls_end_the_turn(tmp_path):
    provider = _Scripted([[("write_file", MALFORMED)]] * 6)
    engine = _engine(tmp_path, provider)
    _run(engine)

    contents = _tool_contents(engine)
    assert len(contents) == 3, f"the turn stopped after three malformed calls, got {len(contents)}"
    assert "Stop: three consecutive tool calls" not in contents[0]
    assert "Stop: three consecutive tool calls" not in contents[1]
    assert "Stop: three consecutive tool calls" in contents[2]


def test_a_readable_call_resets_the_malformed_streak(tmp_path):
    provider = _Scripted(
        [
            [("write_file", MALFORMED)],
            [("write_file", MALFORMED)],
            [("write_file", {"path": "ok.txt", "content": "x"})],
            [("write_file", MALFORMED)],
            [("write_file", MALFORMED)],
        ],
        tail="stopped early",
    )
    engine = _engine(tmp_path, provider)
    _run(engine)

    contents = _tool_contents(engine)
    assert len(contents) == 5  # the clean call reset the streak; no early stop
    assert "Stop: three consecutive tool calls" not in contents[4]
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "x"
