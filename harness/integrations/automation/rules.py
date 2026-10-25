"""Standing approvals: permission to repeat one kind of action without asking each run.

A task's `always_allowed_tools` holds entries of two shapes. A bare tool name allows the tool with
any argument (the older form). `"tool target"`, with one space because tool names never contain
one, allows it against one exact target such as a channel address or a recipient. The rules live
on the task record, so revoking is per automation and deleting the task takes them with it.
"""

from __future__ import annotations

from typing import Any, Optional


def rule_entry(tool: str, target: Optional[str] = None) -> str:
    return f"{tool} {target}" if target else tool


def rule_parts(entry: str) -> tuple[str, Optional[str]]:
    tool, _, target = entry.strip().partition(" ")
    return tool, (target.strip() or None)


def grant_entries(permissions: Any) -> list[str]:
    """The grantable entries in a proposed `permissions` list (from the create tool or the app's
    create request). Only `access: "write"` items can become grants, the tool must declare a target
    argument (which leaves out command-running and destructive tools), and the target must not be
    empty. Reads are shown on the consent card and never stored. Whatever else is dropped."""
    from ..connectors.catalog import target_arg_for

    entries: list[str] = []
    for item in permissions or []:
        if not isinstance(item, dict) or str(item.get("access", "")).lower() != "write":
            continue
        tool = str(item.get("tool", "")).strip()
        target = str(item.get("target", "")).strip()
        if not tool or not target or target_arg_for(tool) is None:
            continue
        entry = rule_entry(tool, target)
        if entry not in entries:
            entries.append(entry)
    return entries
