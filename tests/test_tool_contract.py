"""Tool output contract (harness/tools/contract.py) — the declaration that says how a
tool's RESULT is read, by the model and by a person.

Three properties matter and each gets its own group below:

1. **A tool without a contract behaves exactly as it did.** This is the only reason the
   change can land at all — ~100 builtin tools, 40 connectors, and MCP tools discovered
   at runtime cannot migrate at once. Every un-migrated tool must stay byte-identical.
2. **A declared contract reaches the two seams that matter** — the model-facing tool
   message, and the two events a surface renders a card from.
3. **A broken contract degrades, never breaks.** Presenters are third-party code on the
   turn's hot path; one that raises must cost a log line, not the turn.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import json

import aisuite as ai
import pytest

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.contract import ToolContract, ToolPresentation, contract_of, safe_present_call, safe_present_result, safe_render, tool_contract


# -- harness -------------------------------------------------------------------


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _text_turn(text="done"):
    return AssistantTurn(text=text, finish_reason="stop")


async def _allow(_request):
    return ApprovalOutcome.ONCE


def _run(tmp_path, func, args, *, approver=None, metadata=None):
    """One turn that calls `func` once, then finishes. Returns (events, engine)."""
    registry = ToolRegistry()
    registry.register(func, metadata=metadata or ToolMetadata())
    engine = TurnEngine(
        provider=ScriptedProvider(
            [_tool_turn(func.__name__, args), _text_turn()]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approver,
    )

    async def _go():
        return [ev async for ev in engine.run("go")]

    return asyncio.run(_go()), engine


def _tool_message(engine):
    """The message the MODEL will read for the tool result."""
    return next(m for m in engine.messages if m.get("role") == "tool")


def _event(events, kind):
    return next(ev for ev in events if ev.type is kind)


def _approval_meta():
    """Metadata that routes a tool to the approval card (risk.classify: an aisuite
    `requires_approval` tool with no base classification is EXTERNAL)."""
    return ai.ToolMetadata(
        name="needs_ok",
        category="connector",
        risk_level="medium",
        capabilities=["integration"],
        requires_approval=True,
    )


# -- 1. no contract: nothing changes -------------------------------------------


def test_plain_tool_gets_no_contract():
    def plain_tool(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    registry = ToolRegistry()
    spec = registry.register(plain_tool, metadata=ToolMetadata())
    assert spec.contract is None
    assert registry.contract("plain_tool") is None
    assert contract_of(plain_tool) is None


def test_plain_tool_result_is_still_json(tmp_path):
    def plain_tool(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    _, engine = _run(tmp_path, plain_tool, {"value": "x"})
    assert json.loads(_tool_message(engine)["content"]) == {"ok": "x"}


def test_plain_tool_finished_event_has_no_presentation(tmp_path):
    def plain_tool(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    events, _ = _run(tmp_path, plain_tool, {"value": "x"})
    finished = _event(events, EventType.TOOL_FINISHED)
    assert "presentation" not in finished.data
    assert finished.data["result_preview"]  # the pre-contract fallback still renders


def test_a_cut_preview_comes_with_the_text_the_model_was_given(tmp_path):
    """The preview is the first 300 characters of the result's JSON. A live row drew that
    fragment as the call's output, cut mid-value, until a reload gave it the stored text."""

    def long_tool(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"lines": [f"{value} {number}" for number in range(80)]}

    events, engine = _run(tmp_path, long_tool, {"value": "row"})
    finished = _event(events, EventType.TOOL_FINISHED)
    assert finished.data["result_preview"].endswith("...")
    assert finished.data["result_text"] == _tool_message(engine)["content"]
    assert "row 79" in finished.data["result_text"]


def test_a_whole_preview_is_not_sent_twice(tmp_path):
    def short_tool(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    events, _ = _run(tmp_path, short_tool, {"value": "x"})
    assert "result_text" not in _event(events, EventType.TOOL_FINISHED).data


def test_contract_never_reaches_the_model_schema():
    """`schemas()` is the provider payload: a contract must not leak into it."""

    @tool_contract(render=lambda a, v: "x", timeout_ms=1000)
    def annotated(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    registry = ToolRegistry()
    registry.register(annotated, metadata=ToolMetadata())
    blob = json.dumps(registry.schemas())
    assert "timeout_ms" not in blob and "render" not in blob


# -- 2. a declared contract reaches both seams ---------------------------------


def test_render_replaces_the_model_facing_content(tmp_path):
    @tool_contract(render=lambda args, v: f"{v['count']} hàng, cột: {v['cols']}")
    def query(table: str) -> dict:
        """Query.

        :param table: name
        """
        return {"count": 12, "cols": ["a", "b"], "rows": [[1, 2]] * 12}

    _, engine = _run(tmp_path, query, {"table": "t"})
    assert _tool_message(engine)["content"] == "12 hàng, cột: ['a', 'b']"


def test_render_sees_the_value_after_display_is_lifted(tmp_path):
    """`_display` is user-facing metadata the agent must never see. `render` feeds the
    model, so it must be given the already-cleaned value."""
    seen = {}

    def _render(args, value):
        seen["keys"] = sorted(value)
        return "rendered"

    @tool_contract(render=_render)
    def filtered(q: str) -> dict:
        """Search.

        :param q: query
        """
        return {"hits": 3, "_display": {"hidden_by_filters": 2}}

    events, engine = _run(tmp_path, filtered, {"q": "x"})
    assert seen["keys"] == ["hits"], "render must not see _display"
    assert _tool_message(engine)["content"] == "rendered"
    # …and the sidecar still reaches the surface, unchanged.
    assert _event(events, EventType.TOOL_FINISHED).data["display"] == {
        "hidden_by_filters": 2
    }


def test_present_result_rides_on_tool_finished(tmp_path):
    @tool_contract(
        present_result=lambda args, v: ToolPresentation(
            summary=f"Đã đổi {v['n']} file sang PDF", kind="write"
        )
    )
    def convert(folder: str) -> dict:
        """Convert.

        :param folder: path
        """
        return {"n": 200}

    events, _ = _run(tmp_path, convert, {"folder": "/x"})
    data = _event(events, EventType.TOOL_FINISHED).data
    assert data["presentation"] == {
        "summary": "Đã đổi 200 file sang PDF",
        "kind": "write",
    }
    assert data["result_preview"], "the raw preview stays for surfaces that want it"


def test_present_call_rides_on_permission_required(tmp_path):
    @tool_contract(
        present_call=lambda args: ToolPresentation(
            summary=f"Gửi tin nhắn vào {args['channel']}",
            detail="12 người sẽ thấy",
            kind="send",
        )
    )
    def needs_ok(channel: str, text: str) -> dict:
        """Send.

        :param channel: target
        :param text: body
        """
        return {"sent": True}

    events, _ = _run(
        tmp_path,
        needs_ok,
        {"channel": "#sales", "text": "hi"},
        approver=_allow,
        metadata=_approval_meta(),
    )
    data = _event(events, EventType.PERMISSION_REQUIRED).data
    assert data["presentation"] == {
        "summary": "Gửi tin nhắn vào #sales",
        "detail": "12 người sẽ thấy",
        "kind": "send",
    }
    # The mechanism-level fields stay: the card degrades, it does not lose information.
    assert data["name"] == "needs_ok" and data["arguments"]["channel"] == "#sales"


def test_registry_accepts_a_contract_for_a_function_it_does_not_own():
    """MCP tools and aisuite toolkit tools are built elsewhere; a caller must be able to
    attach a contract without mutating the function object."""

    def borrowed(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    registry = ToolRegistry()
    spec = registry.register(
        borrowed, contract=ToolContract(render=lambda a, v: "from outside")
    )
    assert spec.contract is not None
    assert safe_render(spec.contract, "borrowed", {}, {}) == "from outside"


# -- 3. a broken contract degrades, never breaks --------------------------------


def test_raising_render_falls_back_to_json(tmp_path, caplog):
    def _boom(args, value):
        raise RuntimeError("presenter bug")

    @tool_contract(render=_boom)
    def flaky(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    events, engine = _run(tmp_path, flaky, {"value": "x"})
    assert json.loads(_tool_message(engine)["content"]) == {"ok": "x"}
    assert _event(events, EventType.TURN_END).data["status"] == "completed"


def test_raising_present_result_does_not_break_the_turn(tmp_path):
    def _boom(args, value):
        raise RuntimeError("presenter bug")

    @tool_contract(present_result=_boom)
    def flaky(value: str) -> dict:
        """A tool.

        :param value: anything
        """
        return {"ok": value}

    events, _ = _run(tmp_path, flaky, {"value": "x"})
    finished = _event(events, EventType.TOOL_FINISHED)
    assert "presentation" not in finished.data
    assert finished.data["status"] == "ok"
    assert _event(events, EventType.TURN_END).data["status"] == "completed"


def test_render_returning_a_non_string_falls_back():
    """Coercing would ship whatever `repr` happens to say into the model's context."""
    contract = ToolContract(render=lambda a, v: {"not": "a string"})
    assert safe_render(contract, "t", {}, {}) is None


def test_present_returning_a_non_presentation_falls_back():
    contract = ToolContract(
        present_call=lambda a: "just a string",
        present_result=lambda a, v: 42,
    )
    assert safe_present_call(contract, "t", {}) is None
    assert safe_present_result(contract, "t", {}, {}) is None


def test_safe_helpers_accept_a_missing_contract():
    assert safe_render(None, "t", {}, {}) is None
    assert safe_present_call(None, "t", {}) is None
    assert safe_present_result(None, "t", {}, {}) is None


# -- validation: a malformed declaration fails at import, not at runtime --------


@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_presentation_rejects_an_empty_summary(bad):
    with pytest.raises(ValueError):
        ToolPresentation(summary=bad)


def test_presentation_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        ToolPresentation(summary="ok", kind="destroy")


@pytest.mark.parametrize("bad", [0, -1, 1.5, "30s", True])
def test_contract_rejects_a_bad_timeout(bad):
    with pytest.raises(ValueError):
        ToolContract(timeout_ms=bad)


def test_contract_rejects_a_non_callable_presenter():
    with pytest.raises(TypeError):
        ToolContract(present_call="not callable")


def test_presentation_omits_an_empty_detail():
    assert ToolPresentation(summary="s").as_dict() == {"summary": "s", "kind": "other"}
    assert ToolPresentation(summary="s", detail="d").as_dict()["detail"] == "d"
