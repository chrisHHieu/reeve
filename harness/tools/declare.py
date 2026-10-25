"""Making a plain function into a tool the registry can hold.

The registry reads a tool off attributes of its function: the name and the description the model
sees, the JSON schema of its arguments, and the metadata the permission engine checks. These two
helpers write them in one place, so no tool module sets them by hand.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

import aisuite as ai


def function_schema(name: str, description: str, properties: dict[str, Any], required: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """The `{"type": "function", ...}` schema of a tool with these argument `properties`."""
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = list(required)
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def declare_tool(
    func: Callable[..., Any],
    schema: dict[str, Any],
    *,
    category: str,
    capabilities: Iterable[str],
    risk: str = "low",
    asks: bool = False,
) -> Callable[..., Any]:
    """Name `func` after `schema`, describe it with the schema's description, and attach the
    metadata: `asks` is whether a call waits for the person's approval."""
    spec = schema["function"]
    func.__name__ = spec["name"]
    func.__doc__ = spec["description"]
    func.__tool_schema__ = schema
    func.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name=spec["name"], category=category, risk_level=risk, capabilities=list(capabilities), requires_approval=asks
    )
    return func
