"""The JSON Schema subset a `task` child's structured output may use.

Deliberately small: every keyword here is one the harness itself enforces, so a schema
the model writes can never promise validation we do not actually perform (`pattern`,
`format`, numeric bounds and friends are rejected BY NAME). It mirrors the
`ObjectJsonSchema` consumer view — the structured-output foundation its workflow tool
builds on — without the capture-tool machinery: the child is instructed to answer with
the object, the answer is validated, and one corrective retry is allowed before the
delegation is reported as a structured-output failure.
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: Keywords a schema may use. Unknown ones fail loudly instead of being silently
#: ignored — a schema that asks for something we do not enforce would otherwise read
#: as validated when it is not.
ALLOWED_KEYWORDS = (
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "oneOf",
)
_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
MAX_DEPTH = 16


def validate_output_schema(schema: Any) -> Optional[str]:
    """`None` when `schema` is a usable object-rooted subset schema, else the reason."""
    if not isinstance(schema, dict):
        return "output_schema must be a JSON object"
    if schema.get("type") != "object":
        return 'output_schema must be an object-rooted schema ("type": "object")'
    return _check(schema, "output_schema", 0)


def _check(schema: Any, path: str, depth: int) -> Optional[str]:
    if depth > MAX_DEPTH:
        return f"{path}: schema nesting is too deep (max {MAX_DEPTH})"
    if not isinstance(schema, dict):
        return f"{path}: must be a JSON object"
    for key in schema:
        if key not in ALLOWED_KEYWORDS:
            return (
                f"{path}: unsupported keyword {key!r} — supported: "
                + ", ".join(ALLOWED_KEYWORDS)
            )
    kind = schema.get("type")
    if kind is not None and kind not in _TYPES:
        return f"{path}.type: expected one of {', '.join(_TYPES)}, got {kind!r}"
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            return f"{path}.enum: expected a non-empty array"
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            return f"{path}.properties: expected an object"
        for name, sub in props.items():
            problem = _check(sub, f"{path}.properties.{name}", depth + 1)
            if problem:
                return problem
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or any(not isinstance(n, str) for n in required):
            return f"{path}.required: expected an array of property names"
        unknown = [n for n in required if n not in (schema.get("properties") or {})]
        if unknown:
            return f"{path}.required: names not declared in properties: {', '.join(unknown)}"
    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        return f"{path}.additionalProperties: only true/false is supported"
    if "items" in schema:
        problem = _check(schema["items"], f"{path}.items", depth + 1)
        if problem:
            return problem
    if "oneOf" in schema:
        options = schema["oneOf"]
        if not isinstance(options, list) or not options:
            return f"{path}.oneOf: expected a non-empty array of schemas"
        for index, sub in enumerate(options):
            problem = _check(sub, f"{path}.oneOf[{index}]", depth + 1)
            if problem:
                return problem
    return None


def validate_value(schema: dict, value: Any) -> list[str]:
    """Path-qualified errors for `value` against an already-validated schema."""
    errors: list[str] = []
    _walk(schema, value, "$", errors)
    return errors


def _walk(schema: Any, value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {_describe(value)}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {_describe(value)}")
        return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            bucket: list[str] = []
            _walk(option, value, path, bucket)
            if not bucket:
                matches += 1
        if matches != 1:
            errors.append(
                f"{path}: expected exactly one oneOf branch to match (matched {matches})"
            )
        return
    kind = schema.get("type")
    if kind is not None and not _has_type(value, kind):
        errors.append(f"{path}: expected {kind}, got {_describe(value)}")
        return
    if kind == "object" or (kind is None and isinstance(value, dict)):
        if not isinstance(value, dict):
            return
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        props = schema.get("properties") or {}
        for name, sub in props.items():
            if name in value:
                _walk(sub, value[name], f"{path}.{name}", errors)
        if schema.get("additionalProperties") is False:
            extra = [n for n in value if n not in props]
            if extra:
                errors.append(f"{path}: unexpected properties: {', '.join(sorted(extra))}")
    elif kind == "array" or (kind is None and isinstance(value, list)):
        if not isinstance(value, list):
            return
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _walk(items, item, f"{path}[{index}]", errors)


def parse_structured(schema: dict, text: str) -> tuple[Optional[Any], list[str]]:
    """`(value, errors)` from a child's reply: a fenced JSON block or the widest span."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    value: Any = None
    parsed = False
    try:
        value = json.loads(raw)
        parsed = True
    except ValueError:
        start = min((i for i in (raw.find("{"), raw.find("[")) if i >= 0), default=-1)
        end = max(raw.rfind("}"), raw.rfind("]"))
        if 0 <= start < end:
            try:
                value = json.loads(raw[start : end + 1])
                parsed = True
            except ValueError:
                parsed = False
    if not parsed:
        sample = " ".join(raw.split())[:120]
        return None, [f"$: reply is not valid JSON ({sample!r})"]
    errors = validate_value(schema, value)
    return (None, errors) if errors else (value, [])


def _has_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    return False


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
