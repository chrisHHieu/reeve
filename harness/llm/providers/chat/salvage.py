"""Tool calls a model wrote as text.

Some compatible backends, notably Ollama with several local models, leave the structured
`tool_calls` field empty and put the call in the reply text instead, in a variety of shapes:

- a `<tool_call>{...}</tool_call>` block;
- Qwen-style XML, `<function=NAME><parameter=KEY>VALUE</parameter></function>`;
- DeepSeek's own markup (see `dsml.py`);
- a bare `{"name": ..., "arguments": ...}` object, often inside prose;
- a shorthand, `toolname {args}` or `toolname [args]`.

The loop needs structured calls, so they are recovered here. The tool schemas that were sent
decide what counts: only a name that is a real tool is accepted, which keeps ordinary text
that happens to contain braces from becoming a call. This runs only when tools were offered
and none came back structured, so it never touches a native OpenAI reply.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterator, Optional

from ..base import ToolCall
from ..dsml import salvage_dsml

_TOOL_CALL_OPEN = re.compile(r"<tool_call>\s*", re.IGNORECASE)
# Qwen writes the call as nested tags rather than JSON; qwen3-coder does exactly this.
_FUNCTION_BLOCK = re.compile(
    r"<function\s*=\s*(?P<name>[^>\s]+)\s*>(?P<body>.*?)</function\s*>", re.IGNORECASE | re.DOTALL
)
_PARAMETER_BLOCK = re.compile(
    r"<parameter\s*=\s*(?P<key>[^>\s]+)\s*>(?P<value>.*?)</parameter\s*>", re.IGNORECASE | re.DOTALL
)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _parameter_value(raw: str) -> Any:
    """A parameter's text as written (file content is the usual case), except that a token with
    no whitespace in it that is plain JSON (`3`, `true`, `{"a":1}`) becomes that value."""
    text = raw.strip()
    if text and not any(char.isspace() for char in text):
        value = _json(text)
        if isinstance(value, (dict, list, int, float, bool)):
            return value
    return text


def _tool_index(tools: Optional[list[dict[str, Any]]]) -> tuple[Optional[set[str]], dict[str, Optional[str]]]:
    """(the names of the offered tools, name -> its only parameter). The second lets a bare
    `toolname [args]` become `{parameter: args}` when the tool takes a single parameter."""
    if not tools:
        return None, {}
    names: set[str] = set()
    sole: dict[str, Optional[str]] = {}
    for tool in tools:
        function = (tool or {}).get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        names.add(name)
        schema = function.get("parameters") or {}
        properties = schema.get("properties") or {}
        if len(properties) == 1:
            sole[name] = next(iter(properties))
        else:
            required = schema.get("required") or []
            sole[name] = required[0] if len(required) == 1 else None
    return names, sole


def _balanced(text: str, start: int) -> Optional[str]:
    """The `{...}` or `[...]` that opens at `text[start]`, with quoted strings respected, or
    None when it never closes."""
    opening = text[start]
    closing = "]" if opening == "[" else "}"
    depth = 0
    in_string = escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : position + 1]
    return None


def _objects(text: str) -> Iterator[str]:
    """Every top-level `{...}` in the text, so JSON in the middle of prose is found."""
    position = 0
    while position < len(text):
        if text[position] == "{":
            found = _balanced(text, position)
            if found:
                yield found
                position += len(found)
                continue
        position += 1


def _call_from(value: Any, names: Optional[set[str]]) -> Optional[ToolCall]:
    """A call from a `{"name", "arguments"}` dict, or None when it is not one or the name is
    not an offered tool."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name or (names is not None and name not in names):
        return None
    arguments = value.get("arguments", value.get("parameters"))
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        arguments = _json(arguments)
        if not isinstance(arguments, dict):
            arguments = {"_raw": value.get("arguments")}
    if not isinstance(arguments, dict):
        arguments = {"_raw": arguments}
    return ToolCall(id="", name=name, arguments=arguments)


def _with_ids(calls: list[ToolCall]) -> list[ToolCall]:
    return [
        ToolCall(id=f"call_salvaged_{uuid.uuid4().hex[:16]}", name=call.name, arguments=call.arguments)
        for call in calls
    ]


def _tagged_blocks(text: str, names: Optional[set[str]]) -> list[ToolCall]:
    calls = []
    for opening in _TOOL_CALL_OPEN.finditer(text):
        at = opening.end()
        if at < len(text) and text[at] in "{[":
            found = _balanced(text, at)
            parsed = _json(found) if found else None
            for item in parsed if isinstance(parsed, list) else [parsed]:
                call = _call_from(item, names)
                if call:
                    calls.append(call)
    return calls


def _xml_calls(text: str, names: Optional[set[str]]) -> list[ToolCall]:
    calls = []
    for block in _FUNCTION_BLOCK.finditer(text):
        name = block.group("name").strip()
        if names is not None and name not in names:
            continue
        arguments = {
            parameter.group("key").strip(): _parameter_value(parameter.group("value"))
            for parameter in _PARAMETER_BLOCK.finditer(block.group("body"))
        }
        calls.append(ToolCall(id="", name=name, arguments=arguments))
    return calls


def _embedded_objects(text: str, names: Optional[set[str]]) -> list[ToolCall]:
    calls = []
    for found in _objects(text):
        value = _json(found)
        if isinstance(value, dict) and "name" in value:
            call = _call_from(value, names)
            if call:
                calls.append(call)
    return calls


def _shorthand(text: str, names: set[str], sole: dict[str, Optional[str]]) -> list[ToolCall]:
    """`toolname {args}` / `toolname [args]`, only for tools that were offered, one call per
    tool name."""
    calls = []
    for name in names:
        for match in re.finditer(re.escape(name) + r"\s*[:=]?\s*", text):
            at = match.end()
            if at >= len(text) or text[at] not in "{[":
                continue
            found = _balanced(text, at)
            parsed = _json(found) if found else None
            if parsed is None:
                continue
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                parameter = sole.get(name)
                if not parameter:
                    continue
                arguments = {parameter: parsed}
            calls.append(ToolCall(id="", name=name, arguments=arguments))
            break
    return calls


def salvage_tool_calls_from_text(content: str, tools: Optional[list[dict[str, Any]]] = None) -> list[ToolCall]:
    """The tool calls found in `content`, or [] when nothing in it is tool-shaped. The shapes
    are tried in the order listed in the module docstring; the first that finds any wins."""
    text = (content or "").strip()
    if not text:
        return []
    names, sole = _tool_index(tools)
    for found in (
        _tagged_blocks(text, names),
        _xml_calls(text, names),
        salvage_dsml(text, tools),
        _embedded_objects(text, names),
    ):
        if found:
            return _with_ids(found)
    return _with_ids(_shorthand(text, names, sole)) if names else []


def maybe_salvage_tool_calls(
    text: Optional[str], tool_calls: list[ToolCall], *, tools: Optional[list[dict[str, Any]]]
) -> tuple[Optional[str], list[ToolCall]]:
    """(text, tool calls) after trying to recover calls from the text. Success replaces the
    calls and clears the text; otherwise both are returned as they came."""
    if tool_calls or not tools or not text:
        return text, tool_calls
    salvaged = salvage_tool_calls_from_text(text, tools)
    return (None, salvaged) if salvaged else (text, tool_calls)
