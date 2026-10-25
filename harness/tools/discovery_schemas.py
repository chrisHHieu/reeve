"""The wire schemas of the discovery bridge, and the hint every result repeats. A
search loads its best matches and shows their parameters, so the words say that first.

Split from `discovery.py` (which keeps the behaviour) so that module stays under its size
ceiling. The text is model-facing: a tool is LOADED by `tool_describe` and
called by its own name, from the schema the describe result shows (it is not
added to the tool list, which keeps the cached prefix), so the words teach that path first
and keep `tool_call` as the fallback it still is.
"""

from __future__ import annotations

from typing import Any

TOOL_SEARCH = "tool_search"
TOOL_DESCRIBE = "tool_describe"
TOOL_CALL = "tool_call"

DEFAULT_RESULTS = 5
MAX_RESULTS = 25


def search_hint() -> str:
    return (
        "Call tool_describe(name) to load a tool: it shows the parameters and makes the "
        "tool callable by its own name, so you then call it directly by that name."
    )


SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_SEARCH,
        "description": (
            "Search this session's deferred tools — MCP servers, connectors and other "
            "tools kept off the tool list to save context. Returns matching names and "
            "one-line descriptions; the best matches also come with their parameters and "
            "are loaded, so you call them directly by name in your next step. "
            "tool_describe(name) loads any other match. Call with no query to list "
            "everything available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What you want to do, in a few words (e.g. 'create github "
                        "issue'). Empty lists the deferred catalog."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Max matches (1-{MAX_RESULTS}, default {DEFAULT_RESULTS})."
                    ),
                },
            },
            "required": [],
        },
    },
}

DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_DESCRIBE,
        "description": (
            "Load one deferred tool: shows its full parameter schema and makes it "
            "callable, so from your next step you call it directly by its name with "
            "those parameters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact tool name from tool_search.",
                },
            },
            "required": ["name"],
        },
    },
}

CALL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_CALL,
        "description": (
            "Run a deferred tool by name with its arguments, without loading it first. "
            "A tool loaded by tool_describe is simply called by its own name instead. "
            "The call goes through the same permission and approval path as any other "
            "tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact tool name from tool_search.",
                },
                "arguments": {
                    "type": "object",
                    "description": "The tool's arguments, matching tool_describe.",
                },
            },
            "required": ["name"],
        },
    },
}
