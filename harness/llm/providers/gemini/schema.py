"""Tool parameter schemas in the form Gemini accepts.

Function declarations take an OpenAPI 3.0 subset of JSON Schema. The SDK validates the request
config locally, so a schema outside the subset fails the turn before any HTTP call is made.
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: The schema keys function declarations accept. Others (`additionalProperties`, `$schema`, …)
#: are dropped.
_ACCEPTED_KEYS = {
    "type", "format", "description", "nullable", "enum", "items", "properties", "required",
    "anyOf", "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "pattern",
    "example", "default", "title",
}


def _coerce_enum(schema: dict[str, Any]) -> None:
    """Make an `enum` the SDK will take: it types `Schema.enum` as `list[str]` whatever the
    parameter's own type is.

    A vendor schema can write `{"type": "integer", "enum": [0, 1, 2]}`, which is valid and
    which other providers accept. Here pydantic rejects the integers while validating the
    config, and the turn dies at the client with no HTTP call at all. One such enum in a
    connected tool broke every Gemini turn of that session.

    A string-typed enum is stringified, which loses nothing because the argument goes out as a
    string anyway. A number or boolean enum is not: stringifying it would keep `type: integer`
    while inviting the model to answer `"1"`. Its values are moved into the description
    instead, so the model still learns the limit and the tool still gets the type it asked for.
    """
    values = schema.get("enum")
    if not isinstance(values, list) or all(isinstance(value, str) for value in values):
        return
    if schema.get("type", "string") == "string":
        schema["enum"] = [str(value) for value in values]
        return
    schema.pop("enum")
    allowed = "Allowed values: " + ", ".join(json.dumps(value) for value in values) + "."
    existing = schema.get("description")
    schema["description"] = f"{existing} {allowed}" if existing else allowed


def sanitize_schema(schema: Any) -> Any:
    """`schema` without the keys Gemini rejects, all the way down.

    A `type` written as a list (a JSON Schema union such as `["string", "number"]`, common in
    vendor tool schemas) is reshaped: `null` becomes `nullable`, a single remaining type stays
    `type`, and several become `anyOf`. One such union once failed every Gemini turn in a
    session with that vendor's tools attached.
    """
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ACCEPTED_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: sanitize_schema(sub) for name, sub in value.items()}
        elif key == "items":
            cleaned[key] = sanitize_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned[key] = [sanitize_schema(sub) for sub in value]
        elif key == "type" and isinstance(value, list):
            kinds = [kind for kind in value if kind != "null"]
            if len(kinds) != len(value):
                cleaned["nullable"] = True
            if len(kinds) == 1:
                cleaned["type"] = kinds[0]
            elif kinds:
                cleaned["anyOf"] = [{"type": kind} for kind in kinds]
        else:
            cleaned[key] = value
    _coerce_enum(cleaned)
    return cleaned


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Function schemas as one Gemini tool holding every declaration."""
    declarations = []
    for tool in tools or []:
        function = tool.get("function") or {}
        entry: dict[str, Any] = {"name": function.get("name") or ""}
        if function.get("description"):
            entry["description"] = function["description"]
        parameters = function.get("parameters")
        # A function with no parameters leaves the field out: Gemini rejects an empty object.
        if isinstance(parameters, dict) and parameters.get("properties"):
            entry["parameters"] = sanitize_schema(parameters)
        declarations.append(entry)
    return [{"function_declarations": declarations}] if declarations else []
