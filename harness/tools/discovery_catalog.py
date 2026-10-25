"""The deferred catalog behind progressive discovery: what is deferred, how a
query ranks against it, and the names-only index `tool_search` carries on the wire.

Split out of `tools/discovery.py`, which keeps the bridge itself (the three tool
closures and the intake unwrap). Everything here is a pure reading of the registry.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .registry import ToolRegistry

#: Bound on the deferred-name index `tool_search` carries on the wire; past it the index
#: lists servers with counts instead of tool names.
MAX_INDEX_CHARS = 4_000
#: Deferring pays when the schemas it takes off the wire outweigh the bridge: its three
#: schemas are ~1.6k characters, ~2k with the names index. At 6k of deferrable schema a
#: request carries ~1k tokens less after paying for the bridge, and the search round trip
#: is paid only when a situational tool is needed. It was 16k, set against MCP-heavy
#: installs, which kept every session without MCP fully eager: a coding session carried
#: ~9k characters of terminal, scheduling and git schemas on every request (2026-10-10).
DISCOVERY_MIN_CHARS = 6_000

#: Built-ins that leave the wire with the MCP tools when progressive discovery is on, by
#: their role in a coding product and not by one install's habits: each serves one kind of
#: situation, not the read-edit-run-check loop most tasks are. An interactive REPL
#: (`terminal_*`; the shell runs programs), timers and wakes (scheduling), a past
#: session's turns (`search_history`), a large fan-out (`workflow`), saving a skill, and a
#: notebook's cells (`edit_notebook`; `read_file` reads one). The core loop stays on the wire: files, search,
#: shell, todo, subagents, web, skills, code mode, and `lsp` (the prompt asks for its
#: diagnostics after an edit). Interactive tools stay eager whatever their role
#: (`ask_user`, `request_tool`, `propose_plan`, `update_plan` — the
#: clarify A/B), as do the tools a result or a round tells the model to call next
#: (`shell_task_output`/`_kill`, `goal`).
COLD_BUILTINS = frozenset(
    {
        "terminal_open", "terminal_send", "terminal_read", "terminal_signal",
        "terminal_close", "terminal_list",
        "create_scheduled_task", "list_scheduled_tasks", "update_scheduled_task",
        "delete_scheduled_task", "sleep_until", "wake_on", "wake_on_event",
        "search_history", "workflow", "save_skill", "edit_notebook",
        # Designing a page or screen: one kind of task, and the check after a design
        # write runs whether or not the model ever loads these.
        "design_guide", "check_design", "compare_design",
    }
)

_NAME_RE = re.compile(r"[^a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _NAME_RE.split(text.lower()) if t]


def _description_of(schema: dict[str, Any], fallback: str = "") -> str:
    return str((schema.get("function") or {}).get("description") or fallback)


def _server_of(name: str) -> str:
    """The MCP server a tool came from, or "" for anything else."""
    parts = name.split("__", 2)
    return parts[1] if len(parts) == 3 and parts[0] == "mcp" else ""


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _rank(query: str, name: str, description: str) -> int:
    """Lexical overlap score. Deterministic and dependency-free on purpose: the catalog
    is tens of entries, and a model call or an index would be machinery for nothing."""
    if not query:
        return 0
    query_lower = query.lower().strip()
    name_lower = name.lower()
    name_tokens = set(_tokens(name))
    desc_tokens = set(_tokens(description))
    score = 6 if query_lower in name_lower else 0
    for token in _tokens(query_lower):
        if token in name_tokens:
            score += 3
        elif token in name_lower:
            score += 2
        if token in desc_tokens:
            score += 1
    return score


def _deferred_specs(registry: ToolRegistry) -> list[tuple[str, str]]:
    """(name, description) for every deferred tool, registration order."""
    out: list[tuple[str, str]] = []
    for name in registry.deferred_names():
        spec = registry.get(name)
        if spec is not None:
            out.append((name, _description_of(spec.schema, f"Deferred tool {name}")))
    return out


def catalog_index(registry: ToolRegistry) -> str:
    """The deferred catalog as one line per server (`server: tool, tool`), bounded.

    Past `MAX_INDEX_CHARS` the per-tool names give way to per-server counts — the index
    must never become the payload it replaces.
    """
    groups: dict[str, list[str]] = {}
    for name in registry.deferred_names():
        server = _server_of(name)
        short = name.split("__", 2)[2] if server else name
        spec = registry.get(name)
        # A connector's tools are listed under the connector, so the count fallback below still
        # says "gmail (36 tools)" where it would have folded them into the built-ins.
        heading = server or (spec.catalog if spec is not None else None) or "built-in"
        groups.setdefault(heading, []).append(short)
    if not groups:
        return ""
    head = (
        "Deferred tools (MCP tools are mcp__<server>__<tool>, built-ins their plain name; "
        "tool_describe loads one, then call it by its name):"
    )
    lines = [f"- {server}: {', '.join(names)}" for server, names in groups.items()]
    text = "\n".join([head, *lines])
    if len(text) <= MAX_INDEX_CHARS:
        return text
    counts = [f"- {server} ({len(names)} tools)" for server, names in groups.items()]
    return "\n".join([head, *counts])


#: How many of a search's best matches come back with their parameters, loaded. The
#: store's first uses of a deferred tool went search, describe, call in 74 of 112: the describe
#: was a step spent only to read what the search could have shown.
SEARCH_LOADS = 3
#: The parameters one search shows, all matches together. A match whose schema would pass it is
#: listed by name, as before, and `tool_describe` loads it.
SEARCH_PARAMETERS_CHARS = 6_000


def search_matches(registry: ToolRegistry, query: str, limit: int) -> list[tuple[str, str]]:
    """(name, description) of the deferred tools `query` matches, best first, at most `limit`."""
    scored = sorted(
        ((_rank(query, name, desc), name, desc) for name, desc in _deferred_specs(registry)),
        key=lambda row: (-row[0], row[1]),
    )
    return [(name, desc) for score, name, desc in scored if score > 0][:limit]


def parameters_shown(registry: ToolRegistry, names: list[str]) -> dict[str, dict[str, Any]]:
    """The parameters a search shows for its best matches, by name, within its budget. Pure, so
    `restore_loaded` reaches the same tools a search loaded when the session is rebuilt."""
    shown: dict[str, dict[str, Any]] = {}
    spent = 0
    for name in names[:SEARCH_LOADS]:
        spec = registry.get(name)
        if spec is None or not spec.defer:
            continue
        parameters = (spec.schema.get("function") or {}).get("parameters") or {}
        size = len(json.dumps(parameters, ensure_ascii=False, default=str))
        if spent + size > SEARCH_PARAMETERS_CHARS:
            continue
        spent += size
        shown[name] = parameters
    return shown


def deferred_payload_chars(registry: ToolRegistry) -> int:
    """How many characters of schema the deferrable tools would put on every request —
    what the bridge saves, and what `DISCOVERY_MIN_CHARS` is compared with."""
    total = 0
    for name in registry.deferred_names():
        spec = registry.get(name)
        if spec is not None:
            total += len(json.dumps(spec.schema, ensure_ascii=False, default=str))
    return total
