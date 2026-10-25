"""Progressive tool discovery for native mode.

Native sessions publish every registered schema, and MCP servers are the one source that
grows without bound: measured on a real install, 104 tools = 23,928 tokens/request and 62
of them were MCP. A session that never touches a server still pays for its schemas on
every request.

This module is the bridge that replaces those schemas when the session enables the mode
(`tool_discovery` in Settings):

- `tool_search` — lexical, deterministic search over the deferred catalog. No model call,
  no network, no egress; output is bounded.
- `tool_describe` — the full declared parameter schema for one tool, also bounded; it
  LOADS the tool: its own name is accepted from then on, so the model calls it
  like any other tool, from the schema this result put in the conversation. The schema
  stays off the wire, so a load never changes the cached prefix.
- `tool_call` — transport-only: the engine unwraps it at intake (before the propose
  event, the approval card, the audit and dispatch) so every consumer sees the tool the
  model actually chose, and the assistant message keeps what the model actually called.
  Its own handler exists only as a safety net for a path that bypasses intake (a program
  calling it from `run_code`).

`rewrite_bridge_call` is the unwrap; it validates the target against the registry the
engine owns, so a subagent's copy can only reach what that child can call.

Deliberately not here: interactive tools (`ask_user`, `request_*`, `propose_*`) are never
deferrable — an A/B measured deferring `clarify` collapsing its usage 18/18 → 7/18,
and Reeve's own rule is that the announced surface and the callable one stay the same.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from .capabilities import READ_ONLY_CAPS
from .contract import ToolContract, attach_contract
from .presentation import ToolPresentation
from .discovery_catalog import (  # noqa: F401 — DISCOVERY_MIN_CHARS etc. re-exported
    DISCOVERY_MIN_CHARS,
    MAX_INDEX_CHARS,
    SEARCH_PARAMETERS_CHARS,
    _clip,
    _deferred_specs,
    _description_of,
    _rank,
    _server_of,
    catalog_index,
    deferred_payload_chars,
    parameters_shown,
    search_matches,
)
from .registry import ToolRegistry

from .discovery_schemas import (  # noqa: F401 — the names callers import from here
    CALL_SCHEMA as _CALL_SCHEMA,
    DEFAULT_RESULTS,
    DESCRIBE_SCHEMA as _DESCRIBE_SCHEMA,
    MAX_RESULTS,
    SEARCH_SCHEMA as _SEARCH_SCHEMA,
    TOOL_CALL,
    TOOL_DESCRIBE,
    TOOL_SEARCH,
    search_hint as _search_hint,
)
from .discovery_render import (  # noqa: F401 — the bounds callers import from here
    DESC_CHARS,
    MAX_DESCRIBE_CHARS,
    MAX_LISTED,
    MAX_RENDER_CHARS,
    capped as _capped,
    render_call_error as _render_call_error,
    render_describe as _render_describe,
    render_search as _render_search,
)


def _present(summary: str) -> Callable[[dict[str, Any]], ToolPresentation]:
    def present_call(arguments: dict[str, Any]) -> ToolPresentation:
        return ToolPresentation(summary=summary)

    return present_call


def discovery_tools(registry: ToolRegistry) -> list[Callable[..., Any]]:
    """The three bridge tools, bound to *registry*.

    `build_engine` calls this for the registry it is about to hand the engine, and again
    for a subagent's filtered copy — the closures must search the registry the ENGINE
    owns, never the parent's.
    """

    def tool_search(query: str = "", limit: int = DEFAULT_RESULTS) -> dict[str, Any]:
        catalog = _deferred_specs(registry)
        capped = _capped(limit)
        if not query or not query.strip():
            listed = catalog[:MAX_LISTED]
            return {
                "matches": [
                    {"name": name, "server": _server_of(name), "description": _clip(desc, DESC_CHARS)}
                    for name, desc in listed
                ],
                "total": len(catalog),
                "hint": _search_hint(),
            }
        found = search_matches(registry, query, capped)
        # The best matches come with their parameters and are loaded, so the next step
        # can call one by its name: the describe in between only read what this can show.
        shown = parameters_shown(registry, [name for name, _ in found])
        loaded = [name for name in shown if registry.load(name) or name in registry.loaded]
        matches = [
            {
                "name": name,
                "server": _server_of(name),
                "description": _clip(desc, DESC_CHARS),
                **({"parameters": shown[name], "loaded": True} if name in loaded else {}),
            }
            for name, desc in found
        ]
        hint = _search_hint()
        if loaded:
            hint = (
                f"Loaded: {', '.join(loaded)} — call them directly by name with the parameters "
                "shown. tool_describe(name) loads any other match."
            )
        if not matches:
            hint = (
                f"No deferred tool matched {query!r}. If it is a built-in it is already "
                "on your tool list — call it directly. Otherwise call tool_search with "
                "no query to see what is available."
            )
        return {"matches": matches, "total": len(catalog), "hint": hint}

    def tool_describe(name: str = "") -> dict[str, Any]:
        target = (name or "").strip()
        spec = registry.get(target) if target else None
        if spec is None:
            catalog = _deferred_specs(registry)
            similar = sorted(
                (n for n, _ in catalog if target and target.lower() in n.lower())
            )[:3] or [n for n, _ in catalog[:3]]
            return {
                "error": f"{target!r} is not a registered tool",
                "similar": similar,
            }
        # Describing a deferred tool LOADS it: its own name is accepted, since the
        # bridge's two-level call shape was the one models kept getting wrong (session
        # b3a8491e-9db, 2026-09-28). The schema is this result, not the tool list.
        if spec.defer and registry.load(target):
            hint = f"Loaded: call `{target}` directly by that name, with the parameters above."
        elif spec.defer and target in registry.loaded:
            hint = f"`{target}` is already loaded — call it directly by that name."
        elif spec.defer:
            hint = _search_hint()
        else:
            hint = "This tool is already on your tool list — call it directly."
        return {
            "name": target,
            "description": _description_of(spec.schema, f"Tool {target}"),
            "parameters": (spec.schema.get("function") or {}).get("parameters") or {},
            "deferred": spec.defer,
            "hint": hint,
        }

    def tool_call(name: str = "", arguments: Optional[Any] = None) -> dict[str, Any]:
        # The engine unwraps a real bridge call before it can reach here. Reaching here
        # means a path that bypasses intake — a program calling `tool_call` itself.
        return {
            "error": (
                "tool_call is the wire bridge for deferred tools and is unwrapped by the "
                "session before dispatch. From inside a program call the tool directly: "
                "tools.<name>(...) inside run_code."
            )
        }

    tool_search.__doc__ = "Search the deferred tool catalog."
    tool_describe.__doc__ = "Show one tool's declared parameters."
    tool_call.__doc__ = "Run a deferred tool (unwrapped by the session at intake)."

    attach_contract(
        tool_search,
        ToolContract(
            render=_render_search,
            present_call=_present("Search deferred tools"),
            capabilities=READ_ONLY_CAPS,
        ),
    )
    attach_contract(
        tool_describe,
        ToolContract(
            render=_render_describe,
            present_call=_present("Describe a deferred tool"),
            capabilities=READ_ONLY_CAPS,
        ),
    )
    attach_contract(
        tool_call,
        ToolContract(
            render=_render_call_error,
            present_call=_present("Run a deferred tool"),
            capabilities=READ_ONLY_CAPS,
        ),
    )
    tool_search.__tool_schema__ = _SEARCH_SCHEMA
    tool_search.__tool_live_schema__ = lambda: search_schema(registry)
    tool_describe.__tool_schema__ = _DESCRIBE_SCHEMA
    tool_call.__tool_schema__ = _CALL_SCHEMA
    return [tool_search, tool_describe, tool_call]


def search_schema(registry: ToolRegistry) -> dict[str, Any]:
    """`tool_search`'s wire schema: the static text plus the deferred catalog, NAMES only.

    Every deferred tool's name stays in context and a schema is fetched only on
    demand, so the model knows a server exists before it searches. Without the list, a
    model asked about "my wealthlog balance" saw a generic search tool and nothing saying
    wealthlog was there. Names cost a few tokens each; the schemas they stand for cost
    ~15k per request on the audited install. Grouped by MCP server and deterministic, so
    the bytes change only when the catalog does.
    """
    index = catalog_index(registry)
    if not index:
        return _SEARCH_SCHEMA
    fn = dict(_SEARCH_SCHEMA["function"])
    fn["description"] = f"{fn['description']}\n\n{index}"
    return {**_SEARCH_SCHEMA, "function": fn}


def rewrite_bridge_call(registry: ToolRegistry, tool_call: Any) -> Optional[str]:
    """Unwrap a `tool_call` bridge call IN PLACE; return an error text or None.

    Called at intake, before anything observes the call: the propose event, the approval
    card, the audit and dispatch must all see the tool the model actually chose. The
    assistant message is serialized from the model's own turn before intake runs, so
    history keeps what the model called while the execution sees the real tool.

    Validation is against *registry* — the registry the engine owns — so a child can only
    reach what that child can call.
    """
    if tool_call.name != TOOL_CALL:
        return None
    arguments = tool_call.arguments or {}
    name = arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        return (
            "tool_call needs the tool's `name` (get one from tool_search). Nothing ran."
        )
    name = name.strip()
    inner = arguments.get("arguments")
    if name == TOOL_CALL and isinstance(inner, dict) and isinstance(inner.get("name"), str):
        # The bridge wrapped in itself (session b3a8491e-9db sent it four times): the real
        # call is one level down. One level is unwrapped; deeper is refused below.
        arguments = inner
        name = inner["name"].strip()
    if name == TOOL_CALL:
        return (
            "tool_call cannot call itself. Put the real tool's name in `name` and its "
            'parameters in `arguments`: {"name": "mcp__server__tool", "arguments": '
            '{...}}. Nothing ran.'
        )
    if registry.get(name) is None:
        # The index lists names short (`wealthlog: get_accounts`), and a model calls them
        # that way — the live A/B's first bridge call did. A short name (or
        # `server__tool`) that matches exactly ONE deferred tool resolves to it; an
        # ambiguous one names the candidates instead of guessing.
        suffix = name if name.startswith("__") else f"__{name}"
        candidates = [n for n in registry.deferred_names() if n.endswith(suffix)]
        if len(candidates) == 1:
            name = candidates[0]
        elif candidates:
            return (
                f"`{name}` matches several tools: {', '.join(candidates)}. Call tool_call "
                "with the full name. Nothing ran."
            )
        else:
            return (
                f"`{name}` is not a registered tool. Use tool_search to find the right "
                "name, then tool_describe for its parameters. Nothing ran."
            )
    raw = arguments.get("arguments")
    if raw is None:
        payload: dict[str, Any] = {}
    elif isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str):
        try:
            decoded = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return (
                "tool_call's `arguments` was a string that is not valid JSON. Pass a JSON "
                "object matching tool_describe(name). Nothing ran."
            )
        if not isinstance(decoded, dict):
            return (
                "tool_call's `arguments` must be a JSON object matching "
                "tool_describe(name). Nothing ran."
            )
        payload = decoded
    else:
        return (
            "tool_call's `arguments` must be a JSON object matching tool_describe(name). "
            "Nothing ran."
        )
    tool_call.name = name
    tool_call.arguments = payload
    return None


def restore_loaded(registry: ToolRegistry, messages: Any) -> list[str]:
    """Load what the thread shows this session already loaded; returns the names.

    The loaded set lives on the registry, and a registry is rebuilt with its engine: after
    a restart, or once an idle engine was dropped. The session then went on unable to call
    by name every tool it had been using. A direct call to one was refused as "not loaded
    yet" and the model described it again: a wasted step to arrive where it had been (before
    the tool list kept still it was also two full-price reads, as the list changed and back).

    Read from the thread with the rules the live paths apply, so a rebuilt registry is
    the one that was dropped: `tool_describe` loads the tool it names, and a `tool_call`
    bridge call loads the tool it resolves to. A call by a tool's own name loads nothing,
    here as there.
    """
    if not registry.progressive_discovery:
        return []
    restored: list[str] = []
    for message in messages or ():
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            function = (call or {}).get("function") or {}
            name = function.get("name")
            if name not in (TOOL_SEARCH, TOOL_DESCRIBE, TOOL_CALL):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    continue
            if not isinstance(arguments, dict):
                continue
            if name == TOOL_SEARCH:
                # A search loads its best matches, chosen by a pure ranking, so the
                # rebuilt registry reaches the same ones while its catalog is the same.
                query = str(arguments.get("query") or "").strip()
                if query:
                    found = search_matches(registry, query, _capped(arguments.get("limit")))
                    for target in parameters_shown(registry, [n for n, _ in found]):
                        if registry.load(target):
                            restored.append(target)
                continue
            if name == TOOL_DESCRIBE:
                target = str(arguments.get("name") or "").strip()
            else:
                bridged = _BridgedCall(TOOL_CALL, dict(arguments))
                if rewrite_bridge_call(registry, bridged) is not None:
                    continue  # the bridge refused it then, and nothing was loaded
                target = bridged.name
            if target and registry.load(target):
                restored.append(target)
    return restored


class _BridgedCall:
    """The two fields `rewrite_bridge_call` reads and rewrites."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments
