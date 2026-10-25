"""Turning a plain function into a connector tool.

A connector tool is a function with three things attached: the JSON schema the model sees,
the aisuite metadata the registry reads (category, risk, whether it needs approval), and its
description as the docstring. Every connector builds its tools the same way, so the three
helpers are here once.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import aisuite as ai

from .catalog import approval_for_tool


def function_schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """The function-calling schema for a tool."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def tool_metadata(name: str, *, approval: bool = False, capabilities: Optional[list[str]] = None):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities or ["integration"],
        requires_approval=approval,
    )


def attach(
    fn: Callable[..., Any],
    schema: dict[str, Any],
    *,
    approval: bool = True,
    caps: Optional[list[str]] = None,
) -> Callable[..., Any]:
    """Give `fn` its schema, metadata and description, and return it.

    For a tool the registry lists, the registry's read or write kind decides whether it needs
    approval, so a connector read never asks. The `approval` argument only governs tools the
    registry does not list.
    """
    name = schema["function"]["name"]
    approval = approval_for_tool(name, default=approval)
    fn.__name__ = name
    fn.__tool_schema__ = schema
    fn.__aisuite_tool_metadata__ = tool_metadata(name, approval=approval, capabilities=caps)
    fn.__doc__ = schema["function"]["description"]
    return fn


def clamp(n: Any, default: int = 10, ceiling: int = 20) -> int:
    """A page size: `n` (or `default` when it is empty or zero), at least 1 and at most `ceiling`."""
    return max(1, min(int(n or default), ceiling))
