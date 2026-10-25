"""A loaded deferred tool is callable by its name and stays off the wire.

Loading a described tool put its schema on the tool list, which is the head of the cached prefix:
on the session store (2026-10-01..14) 82 of 102 mid-session tool-list changes were an MCP tool
loaded that way. Now the describe result is where the model reads the schema, its own name is
accepted, and the request keeps the tool list the session opened with. That a load writes no
header is pinned beside the other header rules, in `test_mcp_arming.py`.
"""

from __future__ import annotations

import asyncio

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.tools import ToolRegistry
from harness.tools.contract import READ_ONLY_CAPS, ToolContract, attach_contract
from harness.tools.discovery import TOOL_CALL, TOOL_DESCRIBE, TOOL_SEARCH, discovery_tools, restore_loaded

DEFERRED = "mcp__ledger__get_transactions"


def _fn(name, *, defer=False):
    def fn(**kwargs):
        return {"echoed": kwargs}

    fn.__name__ = name
    fn.__tool_schema__ = {
        "type": "function",
        "function": {
            "name": name,
            "description": "does a thing",
            "parameters": {"type": "object", "properties": {"month": {"type": "string"}}, "required": []},
        },
    }
    if defer:
        fn.__tool_defer__ = True
    attach_contract(fn, ToolContract(capabilities=READ_ONLY_CAPS))
    return fn


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all([_fn("read_file"), _fn(DEFERRED, defer=True)])
    reg.set_progressive_discovery(True)
    reg.register_all(discovery_tools(reg))
    return reg


def _wire(reg: ToolRegistry) -> set[str]:
    return {s["function"]["name"] for s in reg.schemas()}


class _Scripted(ProviderClient):
    """Emits the batches it is given, and keeps the tool names each request carried."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.calls = 0
        self.tool_lists: list[list[str]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        self.tool_lists.append([t["function"]["name"] for t in tools or []])
        if not self._batches:
            return AssistantTurn(text="done", finish_reason="stop")
        calls = [
            ToolCall(id=f"c{self.calls}-{i}", name=name, arguments=arguments)
            for i, (name, arguments) in enumerate(self._batches.pop(0))
        ]
        return AssistantTurn(tool_calls=calls, finish_reason="tool_calls")

    def capabilities(self, model):
        return ModelCapabilities()


async def _approve(_request):
    return ApprovalOutcome.ONCE


def _run(tmp_path, provider) -> TurnEngine:
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.mode = Mode.WORKSPACE_WRITE
    engine = TurnEngine(
        provider=provider,
        registry=_registry(),
        permissions=permissions,
        model="gpt-5.5",
        approver=_approve,
        max_iterations=6,
    )

    async def go():
        return [event async for event in engine.run("do it")]

    asyncio.run(go())
    return engine


def _tool_contents(engine):
    return [str(m["content"]) for m in engine.messages if m.get("role") == "tool"]


def test_a_described_tool_is_loaded_and_callable_but_not_listed():
    reg = _registry()
    result = reg.execute(TOOL_DESCRIBE, {"name": DEFERRED})
    assert DEFERRED in reg.loaded
    assert DEFERRED in reg.directly_callable()
    assert DEFERRED not in _wire(reg)
    assert DEFERRED not in {s["function"]["name"] for _, s in reg.schemas_by_group()}
    # The words must not promise a tool list the request does not carry.
    assert "tool list" not in result["hint"] and "directly" in result["hint"]
    assert result["parameters"]["properties"] == {"month": {"type": "string"}}
    for schema in reg.schemas():
        assert "onto your tool list" not in schema["function"]["description"]
        assert "puts it on your tool list" not in schema["function"]["description"]


def test_the_tool_list_stays_as_the_session_opened_and_the_call_runs(tmp_path):
    provider = _Scripted([[(TOOL_DESCRIBE, {"name": DEFERRED})], [(DEFERRED, {"month": "2026-09"})]])
    engine = _run(tmp_path, provider)
    contents = _tool_contents(engine)
    assert any('"echoed"' in c and "2026-09" in c for c in contents), contents
    assert not any("progressive discovery" in c for c in contents), contents
    assert len(provider.tool_lists) >= 3
    assert all(names == provider.tool_lists[0] for names in provider.tool_lists), provider.tool_lists
    assert DEFERRED not in provider.tool_lists[-1]


def test_a_tool_reached_through_the_bridge_is_callable_by_name_and_still_unlisted(tmp_path):
    provider = _Scripted([
        [(TOOL_CALL, {"name": DEFERRED, "arguments": {"month": "2026-08"}})],
        [(DEFERRED, {"month": "2026-09"})],
    ])
    engine = _run(tmp_path, provider)
    contents = _tool_contents(engine)
    assert any("2026-08" in c for c in contents) and any("2026-09" in c for c in contents), contents
    assert all(names == provider.tool_lists[0] for names in provider.tool_lists), provider.tool_lists


def test_a_search_loads_its_best_matches_and_the_next_step_calls_one(tmp_path):
    """The store's first uses went search, describe, call in 74 of 112; the describe only
    read what the search could show. A search shows its best matches' parameters and loads them,
    the call runs one step later, and the tool list stays as the session opened."""
    reg = _registry()
    result = reg.execute(TOOL_SEARCH, {"query": "transactions"})
    top = result["matches"][0]
    assert top["name"] == DEFERRED and top["loaded"] is True
    assert top["parameters"]["properties"] == {"month": {"type": "string"}}
    assert DEFERRED in reg.directly_callable() and DEFERRED not in _wire(reg)
    rendered = reg.contract(TOOL_SEARCH).render({"query": "transactions"}, result)
    assert '"month"' in rendered and f"Loaded: {DEFERRED}" in rendered

    provider = _Scripted([[(TOOL_SEARCH, {"query": "transactions"})], [(DEFERRED, {"month": "2026-07"})]])
    engine = _run(tmp_path, provider)
    contents = _tool_contents(engine)
    assert any('"echoed"' in c and "2026-07" in c for c in contents), contents
    assert all(names == provider.tool_lists[0] for names in provider.tool_lists), provider.tool_lists


def test_a_listing_loads_nothing_and_a_rebuilt_registry_reloads_what_a_search_loaded():
    reg = _registry()
    reg.execute(TOOL_SEARCH, {})
    assert DEFERRED not in reg.loaded

    thread = [{
        "role": "assistant",
        "tool_calls": [{"id": "s", "function": {"name": TOOL_SEARCH, "arguments": '{"query": "transactions"}'}}],
    }]
    rebuilt = _registry()
    assert restore_loaded(rebuilt, thread) == [DEFERRED]
    assert DEFERRED in rebuilt.directly_callable()


def test_a_childs_copy_keeps_what_was_loaded_off_the_wire():
    reg = _registry()
    reg.load(DEFERRED)
    child = reg.filtered()
    assert DEFERRED in child.loaded
    assert DEFERRED not in _wire(child)
