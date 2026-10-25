"""The context-makeup panel (context_breakdown in the usage sidecar).

The engine estimates — chars/4, the same estimator the compaction trigger uses — what
each request is made of: system prompt, conversation body, tool schemas split MCP vs
built-ins, the skill menu, meta-context. The estimate rides INSIDE the usage sidecar
(stripped with it, so the wire never sees it) and is additive: an older GUI ignores the
key. These tests pin the categories, the carving of the `<system-context>` block back
out of the body, and the strict wire-format invariant the estimate must not disturb.
"""

import asyncio

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import TokenUsage
from harness.tools import ToolRegistry
from harness.context.compaction import estimate_tokens


class _StopProvider(ProviderClient):
    """One stop turn with usage; records the messages it was handed."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return AssistantTurn(
            text="done",
            finish_reason="stop",
            usage=TokenUsage(input=100, output=5),
        )

    def capabilities(self, model):
        return ModelCapabilities()


def _tool(name, schema_args):
    def _fn(**kwargs):
        raise AssertionError("never called")

    _fn.__name__ = name
    _fn.__tool_schema__ = {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": schema_args}},
    }
    return _fn


def _drive(engine, text="hi"):
    async def go():
        async for _ in engine.run(text):
            pass

    asyncio.run(go())


def _assistant(engine) -> dict:
    return [m for m in engine.messages if m.get("role") == "assistant"][-1]


def test_every_category_is_measured_on_the_stored_message(tmp_path):
    registry = ToolRegistry()
    registry.register(_tool("local_tool", {"path": {}}))
    registry.register(_tool("remote_tool", {"query": {}}), group="mcp")
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        context_provider=lambda: "PICK ONE OF THE SKILLS",
        context_parts={"skills": "PICK ONE OF THE SKILLS"},
    )
    engine.messages.append({"role": "system", "content": "SYSTEM RULES " * 20})
    _drive(engine)

    breakdown = _assistant(engine)["usage"]["context_breakdown"]
    assert set(breakdown) == {
        "messages",
        "mcp_tools",
        "system_tools",
        "skills",
        "instructions",
        "system_prompt",
        "meta_context",
    }
    assert breakdown["system_prompt"] > 0
    assert breakdown["messages"] > 0
    assert breakdown["system_tools"] > 0
    assert breakdown["mcp_tools"] > 0
    assert breakdown["skills"] > 0


def test_without_a_context_provider_the_ephemeral_rows_are_zero(tmp_path):
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.messages.append({"role": "system", "content": "SYSTEM RULES " * 20})
    _drive(engine)
    breakdown = _assistant(engine)["usage"]["context_breakdown"]
    assert breakdown["skills"] == 0
    assert breakdown["meta_context"] == 0
    assert breakdown["system_prompt"] > 0
    assert breakdown["messages"] > 0
    assert breakdown["mcp_tools"] == 0
    assert breakdown["system_tools"] == 0


def test_the_stamped_block_is_carved_out_of_the_body(tmp_path):
    """The ephemeral block rides inside the last user message on the wire; the panel
    must not double-count it as conversation body AND as skills/meta-context."""
    big = "SKILL CATALOG ROW " * 200  # ~3.6 KB of ephemeral context
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        context_provider=lambda: big,
        context_parts={"skills": big},
    )
    engine.messages.append({"role": "user", "content": "hi"})
    _drive(engine)

    bd = _assistant(engine)["usage"]["context_breakdown"]
    raw = estimate_tokens(engine.provider.calls[0])
    assert bd["skills"] > 0
    # The tiny "hi" body, once the block is carved out, must be a rounding-distance
    # share of the request — the block's bytes live in skills/meta-context instead.
    assert bd["messages"] < bd["skills"]
    assert bd["messages"] + bd["skills"] + bd["meta_context"] >= raw * 0.95


def test_the_wire_never_sees_the_estimate(tmp_path):
    registry = ToolRegistry()
    registry.register(_tool("local_tool", {"path": {}}))
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    _drive(engine)
    stored = _assistant(engine)
    assert "context_breakdown" in stored["usage"]
    # What the provider was handed carries no usage at all — the sidecar is stripped
    # with its parent, so the new key cannot leak onto the wire either.
    for call in engine.provider.calls:
        assert all("usage" not in m for m in call)


def test_event_payload_carries_the_breakdown(tmp_path):
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def go():
        events = []
        async for event in engine.run("hi"):
            events.append(event)
        return events

    events = asyncio.run(go())
    usage_events = [e for e in events if (e.data or {}).get("usage")]
    assert usage_events, "expected an assistant_message event with usage"
    assert "context_breakdown" in usage_events[-1].data["usage"]


def test_instruction_files_are_their_own_row_wherever_they_ride(tmp_path):
    """One project carried ~16k tokens of AGENTS.md/CLAUDE.md on every request and the panel
    called it "system prompt". A nested file stamped on a turn is counted too."""
    from harness.session.project import render_agents_md
    from harness.context.project_rules import render_rule

    conventions = render_agents_md("project", "Run ruff before committing.\n" * 40)
    rule = render_rule("style.md", "Keep functions short.\n" * 20)
    nested = render_agents_md("nested", "Use the fixtures in conftest.\n" * 30, path="pkg")
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.messages.append({"role": "system", "content": "SYSTEM RULES " * 20 + "\n\n" + conventions + "\n\n" + rule})
    engine.messages.append({"role": "user", "content": "earlier ask\n\n" + nested})
    engine.messages.append({"role": "assistant", "content": "ok"})
    _drive(engine)

    breakdown = _assistant(engine)["usage"]["context_breakdown"]
    expected = (len(conventions) + len(rule)) // 4 + len(nested) // 4
    assert abs(breakdown["instructions"] - expected) <= 2
    from harness.context.compaction import estimate_tokens

    whole_system = estimate_tokens([m for m in engine.messages if m.get("role") == "system"])
    in_system = (len(conventions) + len(rule)) // 4
    assert abs(breakdown["system_prompt"] - (whole_system - in_system)) <= 2  # carved out, not added
    assert breakdown["messages"] > 0


def test_a_prompt_with_no_instruction_files_has_none(tmp_path):
    engine = TurnEngine(
        provider=_StopProvider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.messages.append({"role": "system", "content": "<environment>\nos: x\n</environment>"})
    _drive(engine)
    assert _assistant(engine)["usage"]["context_breakdown"]["instructions"] == 0
