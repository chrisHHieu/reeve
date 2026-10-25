"""DeepSeek's own tool-call markup, read back out of assistant TEXT.

DeepSeek V3.2 and V4 write a tool call as

    <｜DSML｜function_calls>
    <｜DSML｜invoke name="replace_in_file">
    <｜DSML｜parameter name="path" string="true">a.py</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜function_calls>

and a gateway turns that into a structured `tool_calls` entry. When it does not, the markup arrives as the
reply's text, and in a damaged form: the opening `<｜DSML｜function_calls>` / `<｜DSML｜invoke name=...>` tokens
are gone (the tool's name is left as `<parameter name="replace_in_file">`), some tags keep the `｜DSML｜` prefix
with a space after it and some lose it. Measured on real sessions (2026-10-02): five replies from
`deepseek-v4.1-flash` through opencode-go and OpenRouter, each `finish_reason=stop` with no tool call, so the
turn ended "completed" with nothing run and the markup on screen.

The two shapes are read here. Strict, as every other salvage in `chat/salvage.py` is: a name that is not a
requested tool, a parameter the tool does not declare, or a call with no closing `</invoke>` is not a call, and
the text stays text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .base import ToolCall

# `<｜DSML｜ parameter` / `</｜DSML｜ invoke` / `<｜DSML｜function_calls`: the prefix, whatever follows it.
_DECORATION = re.compile(r"<(/?)\s*｜DSML｜\s*", re.IGNORECASE)
_INVOKE = re.compile(r'<invoke\s+name\s*=\s*"(?P<name>[^"]+)"\s*>(?P<body>.*?)</invoke\s*>', re.IGNORECASE | re.DOTALL)
# The invoke's opening tag lost: its tool name sits in a `<parameter name="TOOL">` that has no value of its own
# and is followed straight away by the real parameters.
_LOST_HEAD = re.compile(r'<parameter\s+name\s*=\s*"(?P<name>[^"]+)"\s*>\s*(?=<parameter\s+name\s*=)', re.IGNORECASE)
_PARAMETER = re.compile(
    r'<parameter\s+name\s*=\s*"(?P<key>[^"]+)"(?P<attrs>[^>]*)>(?P<value>.*?)</parameter\s*>',
    re.IGNORECASE | re.DOTALL,
)
_CLOSE = re.compile(r"</invoke\s*>", re.IGNORECASE)


def looks_like_dsml(text: str) -> bool:
    return "｜DSML｜" in text or bool(_LOST_HEAD.search(text))


def salvage_dsml(text: str, tools: Optional[list[dict[str, Any]]]) -> list[ToolCall]:
    """The tool calls written as DSML in `text`, or [] (leave it as text) when there is none, or any of it is
    not exactly a call to a requested tool."""
    if not text or not tools or not looks_like_dsml(text):
        return []
    declared = _declared(tools)
    if not declared:
        return []
    normalised = _DECORATION.sub(r"<\1", text)

    found: list[tuple[str, str]] = [(m.group("name"), m.group("body")) for m in _INVOKE.finditer(normalised)]
    if not found:
        for head in _LOST_HEAD.finditer(normalised):
            close = _CLOSE.search(normalised, head.end())
            if close is None:
                continue  # a call that never closes is a call that was cut off
            found.append((head.group("name"), normalised[head.end() : close.start()]))

    calls: list[ToolCall] = []
    for name, body in found:
        properties = declared.get(name)
        if properties is None:
            return []  # one name that is not a tool: this is prose that mentions a tag, not a call
        arguments: dict[str, Any] = {}
        for parameter in _PARAMETER.finditer(body):
            key = parameter.group("key")
            if properties and key not in properties:
                return []
            arguments[key] = _value(parameter.group("value"), parameter.group("attrs"))
        if not arguments:
            return []
        calls.append(ToolCall(id="", name=name, arguments=arguments))
    return calls


def _declared(tools: list[dict[str, Any]]) -> dict[str, set[str]]:
    """{tool name: the parameter names its schema declares} from OpenAI tool schemas."""
    out: dict[str, set[str]] = {}
    for tool in tools:
        function = (tool or {}).get("function") or {}
        name = function.get("name")
        if isinstance(name, str) and name:
            out[name] = set(((function.get("parameters") or {}).get("properties") or {}).keys())
    return out


def _value(raw: str, attrs: str) -> Any:
    """A parameter's value. `string="false"` marks JSON (a number, a flag, a list); anything else is text,
    kept as written apart from the one newline the markup puts around it."""
    text = raw[1:] if raw.startswith("\n") else raw
    text = text[:-1] if text.endswith("\n") else text
    if re.search(r'string\s*=\s*"false"', attrs or "", re.IGNORECASE):
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text.strip()
    return text
