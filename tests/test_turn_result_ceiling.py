"""Bounding ONE tool result before the turn's aggregate budget is consulted.

Every built-in tool caps what it returns (shell and web fetch 20k, read 50KB), but MCP
and connector tools are third-party code and some return whatever their server sends.
The aggregate budget (`STEP_OUTPUT_BUDGET`) only starts counting once a step has already
crossed 120k, so a single giant blob used to arrive whole — the per-result ceiling is the
outer bound that catches it. The line is 2 000 lines / 50 KB per result for a text-shaped harness.
"""

from __future__ import annotations

from harness.loop import STEP_OUTPUT_BUDGET, TurnEngine
from harness.loop.limits import SHRUNK_RESULT_BUDGET as _SHRUNK_RESULT_BUDGET
from harness.tools.retention import RESULT_CEILING_CHARS
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import ToolCall
from harness.tools import ToolRegistry


def _engine(tmp_path) -> TurnEngine:
    return TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="x",
    )


def _call(name="mcp__server__huge") -> ToolCall:
    return ToolCall(id="c1", name=name, arguments={})


def _text_of(rendered, result) -> str:
    # Mirrors `_tool_result_message`: a contract render is used as-is, a raw string result
    # is used as-is, and only a structured result is JSON-encoded.
    if isinstance(rendered, str):
        return rendered
    if isinstance(result, str):
        return result
    import json

    return json.dumps(result, default=str)


def test_a_single_oversized_result_is_bounded_even_as_the_first_call(tmp_path):
    engine = _engine(tmp_path)
    value = "x" * (RESULT_CEILING_CHARS * 3)

    rendered, result = engine._enforce_turn_budget(_call(), None, value)
    text = _text_of(rendered, result)

    assert len(text) <= RESULT_CEILING_CHARS
    assert "omitted from the middle" in text
    assert text.lstrip('"').startswith("x")  # the head survives


def test_an_ordinary_result_passes_through_untouched(tmp_path):
    """The ceiling only ever catches the uncapped — a normal result is not rewritten
    (rewriting it would spend a spill write and break the model's trust in the text)."""
    engine = _engine(tmp_path)
    value = {"ok": True, "data": "y" * 1_000}

    rendered, result = engine._enforce_turn_budget(_call(), None, value)
    assert rendered is None
    assert result == value


def test_once_the_step_crosses_the_aggregate_budget_later_results_shrink(tmp_path):
    engine = _engine(tmp_path)
    engine._step_output_chars = STEP_OUTPUT_BUDGET + 1
    value = {"data": "z" * 10_000}

    rendered, result = engine._enforce_turn_budget(_call(), None, value)
    text = _text_of(rendered, result)

    assert len(text) <= _SHRUNK_RESULT_BUDGET


def test_a_contract_render_is_bounded_without_losing_the_raw_result(tmp_path):
    """When the tool declares a presentation, the model gets the bounded text while the
    card still renders from the whole value — the same split the aggregate budget uses."""
    engine = _engine(tmp_path)
    rendered_value = "r" * (RESULT_CEILING_CHARS * 2)
    raw = {"path": "big.txt"}

    rendered, result = engine._enforce_turn_budget(_call("read_file"), rendered_value, raw)

    assert isinstance(rendered, str)
    assert len(rendered) <= RESULT_CEILING_CHARS
    assert result is raw
