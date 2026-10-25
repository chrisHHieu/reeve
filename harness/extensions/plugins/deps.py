"""Plugins that need other plugins (design §8.6).

`dependencies` in a manifest: `"name"` (same marketplace), `"name@marketplace"`, or
`{name, marketplace?, version?}`. A plugin whose dependency is off or missing does not
load, and says which; a cycle loads none of its members. Installing stages the missing
dependencies with the plugin, so one consent covers the set.
"""

from __future__ import annotations

from typing import Any


def dependency_ids(raw: dict[str, Any], own_id: str) -> list[str]:
    """The ids a manifest depends on, a bare name resolved in `own_id`'s marketplace."""
    origin = own_id.rsplit("@", 1)[1] if "@" in own_id else ""
    out: list[str] = []
    for item in raw.get("dependencies") or []:
        if isinstance(item, str):
            name = item.strip()
            ident = name if "@" in name else f"{name}@{origin}"
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            ident = f"{item['name']}@{item.get('marketplace') or origin}"
        else:
            continue
        if ident and ident != own_id and ident not in out:
            out.append(ident)
    return out


def _cycle_members(graph: dict[str, list[str]]) -> set[str]:
    members: set[str] = set()
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                members.update(path[path.index(nxt):] if nxt in path else [nxt])
            elif state.get(nxt) is None:
                visit(nxt, [*path, nxt])
        state[node] = 2

    for node in graph:
        if state.get(node) is None:
            visit(node, [node])
    return members


def prune(out: Any) -> None:
    """Drop every loaded plugin whose dependencies are not all loaded (repeating until
    nothing changes), and every plugin in a dependency cycle; each gets an error."""
    graph = {p.id: list(p.dependencies) for p in out.plugins}
    dropped: dict[str, str] = {
        pid: "it is part of a dependency cycle" for pid in _cycle_members(graph)
    }
    changed = True
    while changed:
        changed = False
        loaded = {pid for pid in graph if pid not in dropped}
        for pid in sorted(loaded):
            missing = [d for d in graph[pid] if d not in loaded]
            if missing:
                dropped[pid] = f"it needs {', '.join(missing)}, which is off or not installed"
                changed = True
    if not dropped:
        return
    out.plugins = [p for p in out.plugins if p.id not in dropped]
    out.skill_roots = [r for r in out.skill_roots if r.plugin_id not in dropped]
    out.commands = [c for c in out.commands if c.plugin_id not in dropped]
    out.agents = {n: a for n, a in out.agents.items() if a.get("_plugin") not in dropped}
    out.errors += [{"id": pid, "error": why} for pid, why in sorted(dropped.items())]
