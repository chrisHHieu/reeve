"""Which calls only look is the engine's verdict, on the call.

The GUI folded a run of "looking" calls into one "Explored" line by a list of tool names of its own:
it still held three git tools since removed, and never `git status` run through the shell. The
engine now says it on the call (`looks` on the frames, `_looks` on the stored result): a presenter
that reads or searches, on a tool with no side effects, or a shell command that only reads inside
the roots. A web search reads too, but its query leaves the machine: it stays a row.
"""

from __future__ import annotations

import asyncio

from aisuite.agents import ToolMetadata

from harness.interface.server.manager.session_roots import _with_looks
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.wire import SIDECARS
from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry
from harness.tools.contract import ToolContract, attach_contract
from harness.tools.presentation import ToolPresentation


def _presented(func, kind):
    return attach_contract(
        func,
        ToolContract(
            present_call=lambda arguments: ToolPresentation(summary=func.__name__, kind=kind),
            present_result=lambda arguments, value: ToolPresentation(summary=func.__name__, kind=kind),
        ),
    )


def read_file(path: str) -> str:
    """Read a file."""
    return "text"


def write_file(path: str, content: str) -> str:
    """Write a file."""
    return "ok"


def web_search(query: str) -> str:
    """Search the web."""
    return "hits"


def run_shell(command: str) -> str:
    """Run a command."""
    return "clean"


class _Provider(ProviderClient):
    def __init__(self, calls):
        self._turns = [AssistantTurn(tool_calls=calls, finish_reason="tool_calls")]

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0) if self._turns else AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _run(tmp_path, calls):
    registry = ToolRegistry()
    registry.register(_presented(read_file, "read"), metadata=ToolMetadata())
    registry.register(_presented(write_file, "write"), metadata=ToolMetadata(requires_approval=True))
    registry.register(_presented(web_search, "search"), metadata=ToolMetadata())
    registry.register(_presented(run_shell, "execute"), metadata=ToolMetadata(requires_approval=True))

    async def approve(_request):
        return ApprovalOutcome.ONCE

    engine = TurnEngine(
        provider=_Provider(calls),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS),
        model="x",
        approver=approve,
    )

    async def go():
        return [event async for event in engine.run("go")]

    events = asyncio.run(go())
    return engine, events


def test_the_engine_says_which_calls_only_look_live_and_stored(tmp_path):
    engine, events = _run(
        tmp_path,
        [
            ToolCall(id="r1", name="read_file", arguments={"path": "a.py"}),
            ToolCall(id="s1", name="run_shell", arguments={"command": "git status"}),
            ToolCall(id="w1", name="write_file", arguments={"path": "b.py", "content": "x"}),
            ToolCall(id="q1", name="web_search", arguments={"query": "x"}),
        ],
    )
    finished = {e.data["call_id"]: e.data for e in events if e.type == EventType.TOOL_FINISHED}
    proposed = {e.data["call_id"]: e.data for e in events if e.type == EventType.TOOL_PROPOSED}
    looked = {cid for cid, data in finished.items() if data.get("looks")}
    assert looked == {"r1", "s1"}
    assert {cid for cid, data in proposed.items() if data.get("looks")} == {"r1", "s1"}
    stored = {m["tool_call_id"] for m in engine.messages if m.get("role") == "tool" and m.get("_looks")}
    assert stored == {"r1", "s1"}


def test_the_mark_never_reaches_a_provider():
    assert "_looks" in SIDECARS


def test_an_older_session_gets_the_mark_from_its_stored_kind_and_class():
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "a", "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "b", "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "c", "function": {"name": "todo_write", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "x", "_presentation": {"summary": "r", "kind": "read"}},
        {"role": "tool", "tool_call_id": "b", "content": "x", "_presentation": {"summary": "s", "kind": "search"}},
        {"role": "tool", "tool_call_id": "c", "content": "x", "_presentation": {"summary": "t", "kind": "other"}},
    ]
    shown = _with_looks(messages)
    assert [m.get("_looks", False) for m in shown if m["role"] == "tool"] == [True, False, False]
    assert "_looks" not in messages[1]  # the stored thread is not touched
