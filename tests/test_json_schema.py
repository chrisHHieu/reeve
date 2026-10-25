"""The structured-output JSON Schema subset (`harness/tools/json_schema.py`).

Every keyword the subset accepts must be one the harness actually enforces — so the
tests pin the loud rejection of anything else, the path-qualified value errors, and the
tolerant reply parsing (fences and prose around the object).
"""

from __future__ import annotations

from harness.tools.json_schema import parse_structured, validate_output_schema, validate_value

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer"],
    "additionalProperties": False,
}


def test_the_subset_gate_namess_what_it_rejects():
    assert validate_output_schema({"type": "object"}) is None
    assert validate_output_schema({"type": "object", "properties": {"a": {"enum": [1]}}}) is None

    assert "object-rooted" in validate_output_schema({"type": "array"})
    assert "must be a JSON object" in validate_output_schema("nope")

    problem = validate_output_schema(
        {"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}}
    )
    assert problem and "unsupported keyword 'pattern'" in problem

    problem = validate_output_schema(
        {"type": "object", "properties": {}, "required": ["missing"]}
    )
    assert problem and "not declared in properties" in problem

    problem = validate_output_schema(
        {"type": "object", "additionalProperties": {"type": "string"}}
    )
    assert problem and "true/false" in problem

    problem = validate_output_schema(
        {
            "type": "object",
            "properties": {"a": {"oneOf": [{"type": "string"}, {"format": "email"}]}},
        }
    )
    assert problem and "format" in problem


def test_values_are_checked_with_paths():
    assert validate_value(SCHEMA, {"answer": 1}) == []
    assert validate_value(SCHEMA, {"answer": 1, "items": ["a", "b"]}) == []

    errors = validate_value(SCHEMA, {"answer": "no"})
    assert errors == ["$.answer: expected integer, got string"]

    errors = validate_value(SCHEMA, {})
    assert "$: missing required property 'answer'" in errors

    errors = validate_value(SCHEMA, {"answer": 1, "extra": 2})
    assert "$: unexpected properties: extra" in errors

    errors = validate_value(SCHEMA, {"answer": 1, "items": ["ok", 2]})
    assert errors == ["$.items[1]: expected string, got integer"]


def test_oneof_needs_exactly_one_branch():
    schema = {
        "type": "object",
        "properties": {"v": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
        "required": ["v"],
    }
    assert validate_value(schema, {"v": "s"}) == []
    assert "exactly one" in validate_value(schema, {"v": True})[0]

    overlapping = {
        "type": "object",
        "properties": {"v": {"oneOf": [{"type": "integer"}, {"type": "number"}]}},
        "required": ["v"],
    }
    assert "matched 2" in validate_value(overlapping, {"v": 3})[0]


def test_parse_structured_handles_fences_and_prose():
    value, errors = parse_structured(SCHEMA, '```json\n{"answer": 2}\n```')
    assert value == {"answer": 2} and errors == []

    value, errors = parse_structured(SCHEMA, 'Sure — take this: {"answer": 3}, thanks!')
    assert value == {"answer": 3} and errors == []

    value, errors = parse_structured(SCHEMA, "no json here")
    assert value is None and "not valid JSON" in errors[0]

    value, errors = parse_structured(SCHEMA, '{"answer": "no"}')
    assert value is None and errors == ["$.answer: expected integer, got string"]
