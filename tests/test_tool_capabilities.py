"""Declared tool capabilities.

What is pinned here:
- a declaration decides parallel scheduling for its tool, and a per-call override decides
  it per call;
- a tool that declares NOTHING keeps the engine's old derivation (low risk, no approval) —
  the fallback is what makes adoption incremental instead of a flag day;
- the two danger rules hold: a destructive or side-effecting tool never overlaps, and a
  declaration can never clear a human approval gate;
- MCP annotations are used for scheduling only, never for approval.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import aisuite as ai

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.tools import ToolRegistry
from harness.tools.contract import READ_ONLY_CAPS, SYSTEM_EXEC_CAPS, WORKSPACE_WRITE_CAPS, ToolCapabilities, ToolContract, attach_contract


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, registry: ToolRegistry) -> TurnEngine:
    return TurnEngine(
        provider=_Provider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


def _registered(tmp_path, name: str, contract: ToolContract, *, metadata=None) -> TurnEngine:
    registry = ToolRegistry()

    def fn(path: str = "") -> str:
        return "ok"

    fn.__name__ = name
    if metadata is not None:
        fn.__aisuite_tool_metadata__ = metadata
    attach_contract(fn, contract)
    registry.register_all([fn])
    return _engine(tmp_path, registry)


# -- the declaration decides -----------------------------------------------------------


def test_a_declared_read_only_tool_may_overlap(tmp_path):
    engine = _registered(tmp_path, "peek", ToolContract(capabilities=READ_ONLY_CAPS))
    assert engine._parallel_safe(ToolCall(id="1", name="peek", arguments={}))


def test_a_declared_write_never_overlaps(tmp_path):
    engine = _registered(
        tmp_path, "save", ToolContract(capabilities=WORKSPACE_WRITE_CAPS)
    )
    assert not engine._parallel_safe(ToolCall(id="1", name="save", arguments={}))


def test_a_declared_shell_is_destructive_and_never_overlaps(tmp_path):
    engine = _registered(tmp_path, "sh", ToolContract(capabilities=SYSTEM_EXEC_CAPS))
    assert not engine._parallel_safe(ToolCall(id="1", name="sh", arguments={}))


def test_a_tool_that_declares_nothing_keeps_the_old_derivation(tmp_path):
    """The fallback is the whole reason this can ship incrementally: an unannotated
    low-risk read still overlaps, and an unannotated approval-gated tool still does not."""
    low = ai.ToolMetadata(
        name="peek", category="filesystem", risk_level="low", requires_approval=False
    )
    gated = ai.ToolMetadata(
        name="save", category="filesystem", risk_level="low", requires_approval=True
    )
    engine = _registered(tmp_path, "peek", ToolContract(), metadata=low)
    assert engine._parallel_safe(ToolCall(id="1", name="peek", arguments={}))
    engine = _registered(tmp_path, "save", ToolContract(), metadata=gated)
    assert not engine._parallel_safe(ToolCall(id="1", name="save", arguments={}))


# -- the per-call override ------------------------------------------------------------


def test_a_resolver_can_answer_per_call(tmp_path):
    """Permission capability: a tool whose safety depends on the ARGUMENTS
    (a shell command classified read-only) answers for the call, not for the tool."""
    caps = ToolCapabilities(
        read_only=False,
        destructive=True,
        concurrent_safe=False,
        side_effect_scope="system",
        resolve=lambda args: READ_ONLY_CAPS
        if str(args.get("command", "")).startswith("cat ")
        else None,
    )
    engine = _registered(tmp_path, "sh", ToolContract(capabilities=caps))
    assert engine._parallel_safe(ToolCall(id="1", name="sh", arguments={"command": "cat x"}))
    assert not engine._parallel_safe(
        ToolCall(id="2", name="sh", arguments={"command": "rm -rf x"})
    )


def test_a_broken_resolver_falls_back_to_the_declaration(tmp_path):
    def boom(_args):
        raise RuntimeError("resolver bug")

    caps = replace(SYSTEM_EXEC_CAPS, resolve=boom)
    engine = _registered(tmp_path, "sh", ToolContract(capabilities=caps))
    assert not engine._parallel_safe(ToolCall(id="1", name="sh", arguments={})), (
        "a buggy resolver must degrade to the conservative declaration"
    )


def test_a_resolver_returning_the_wrong_type_is_ignored(tmp_path):
    caps = replace(SYSTEM_EXEC_CAPS, resolve=lambda _args: "read-only, promise")
    engine = _registered(tmp_path, "sh", ToolContract(capabilities=caps))
    assert not engine._parallel_safe(ToolCall(id="1", name="sh", arguments={}))


# -- the declaration cannot clear an approval -----------------------------------------


def test_capabilities_do_not_clear_an_approval_gate(tmp_path):
    """`needs_approval=False` is a statement about the tool, not a grant: the permission
    engine still judges the call by risk and the guard stage. A third-party server
    claiming "read-only" must not turn a human gate into an auto-run."""
    registry = ToolRegistry()

    def snoop(path: str = "") -> str:
        return "ok"

    snoop.__name__ = "snoop"
    snoop.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="snoop", category="mcp", risk_level="medium", requires_approval=True
    )
    attach_contract(
        snoop,
        ToolContract(
            capabilities=ToolCapabilities(
                read_only=True, concurrent_safe=True, side_effect_scope="none"
            )
        ),
    )
    registry.register_all([snoop])
    engine = _engine(tmp_path, registry)
    decision = engine.permissions.evaluate(
        "snoop", {}, snoop.__aisuite_tool_metadata__
    )
    assert not decision.allowed and decision.needs_user, (
        "the declaration did not turn an ask into an allow"
    )


# -- MCP annotations ------------------------------------------------------------------


def _mcp_tool(name="query", **hints):
    return SimpleNamespace(
        name=name,
        description="reads a row",
        inputSchema={"type": "object", "properties": {}},
        annotations=SimpleNamespace(**hints) if hints else None,
    )


def _mcp_callable(tmp_path, mcp_tool):
    from harness.extensions.mcp.config import MCPServerDef
    from harness.extensions.mcp.tools import build_callables

    loop = __import__("asyncio").new_event_loop()
    server = MCPServerDef(
        name="srv", transport="stdio", command="x", requires_approval=False
    )

    async def call_async(_remote, _kwargs):
        return "ok"

    return build_callables(server, [mcp_tool], call_async, loop)[0]


def test_a_read_only_idempotent_mcp_tool_may_overlap(tmp_path):
    fn = _mcp_callable(tmp_path, _mcp_tool(readOnlyHint=True, idempotentHint=True))
    caps = fn.__tool_contract__.capabilities
    assert caps is not None and caps.parallel_safe()


def test_a_destructive_mcp_hint_is_never_parallel(tmp_path):
    fn = _mcp_callable(
        tmp_path, _mcp_tool(readOnlyHint=True, destructiveHint=True, idempotentHint=True)
    )
    caps = fn.__tool_contract__.capabilities
    assert caps is not None and not caps.parallel_safe()


def test_an_mcp_tool_with_no_hints_declares_nothing(tmp_path):
    fn = _mcp_callable(tmp_path, _mcp_tool())
    assert fn.__tool_contract__.capabilities is None
