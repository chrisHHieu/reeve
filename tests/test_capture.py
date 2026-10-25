"""The capture tool: a structured child COMMITS its object instead of hoping the host
can read it out of the final text (`harness/tools/capture.py`)."""

from __future__ import annotations

from harness.tools.capture import CAPTURE_TOOL, capture_correction, capture_decider, capture_instruction, capture_tool
from harness.tools.json_schema import validate_value

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_the_instruction_names_the_tool_and_carries_the_schema():
    text = capture_instruction(SCHEMA)
    assert CAPTURE_TOOL in text and "ONCE" in text
    assert '"answer"' in text
    correction = capture_correction(SCHEMA, ["$.answer: expected integer, got string"])
    assert CAPTURE_TOOL in correction and "expected integer" in correction


def test_a_valid_capture_commits_and_an_invalid_one_is_a_retryable_error():
    sink: dict = {}
    body = capture_tool(SCHEMA, sink)

    rejected = body(value={"answer": "seven"})
    assert rejected["retryable"] is True and "expected integer" in rejected["error"]
    assert sink == {}

    accepted = body(value={"answer": 7})
    assert accepted["captured"] is True
    assert sink["value"] == {"answer": 7}


def test_the_model_facing_schema_carries_the_required_shape():
    tool = capture_tool(SCHEMA, {})
    schema = tool.__tool_schema__["function"]
    assert schema["name"] == CAPTURE_TOOL
    value = schema["parameters"]["properties"]["value"]
    assert value["required"] == ["answer"]
    assert value["additionalProperties"] is False


def test_a_committed_capture_closes_the_turn():
    sink = {"value": {"answer": 1}}
    calls: list[str] = []

    def inner(tool_name: str, arguments: dict):
        calls.append(tool_name)
        return "allow"

    decide = capture_decider(inner, sink)
    assert decide("run_shell", {}) == "deny"
    assert "run_shell" not in calls, "a denied call never reaches the inner ruleset"
    assert decide(CAPTURE_TOOL, {}) == "allow"

    sink["value"] = None
    assert decide("run_shell", {}) == "allow"
    assert calls == [CAPTURE_TOOL, "run_shell"]


def test_the_error_validator_and_the_capture_tool_agree_on_shape():
    """The tool's rejection is the same `validate_value` the fallback report uses — one
    definition of "matches the schema" for both doors."""
    assert validate_value(SCHEMA, {"answer": "x"})[0].startswith("$.answer")
