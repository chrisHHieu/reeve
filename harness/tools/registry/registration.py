"""Putting tools into a registry, taking them out, and copying it for another agent."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from ..contract import ToolContract, contract_of
from .spec import ToolSpec, schema_for


class RegistrationMixin:
    def register(
        self,
        func: Callable[..., Any],
        *,
        metadata: Any = None,
        schema: Optional[dict[str, Any]] = None,
        contract: Optional[ToolContract] = None,
        group: Optional[str] = None,
        defer: bool = False,
        session_bound: bool = False,
    ) -> ToolSpec:
        name = getattr(func, "__name__", None)
        if not name:
            raise ValueError("Tool function must have a __name__.")
        # Several things travel with the callable and are read off it here, so a tool built at
        # runtime carries its own facts and no caller has to remember to say them. Forgetting is how
        # a stale tool survives a resync.
        #
        # A schema can be given as an argument or as a `__tool_schema__` attribute, for a tool
        # whose signature cannot be turned into a valid JSON schema automatically. An explicit
        # contract wins in the same way, which lets a caller attach one to a function it does not own
        # (an aisuite toolkit tool, an MCP tool built at runtime).
        live = getattr(func, "__tool_live_schema__", None)
        spec = ToolSpec(
            name=name,
            schema=schema or getattr(func, "__tool_schema__", None) or schema_for(func),
            func=func,
            metadata=metadata or getattr(func, "__aisuite_tool_metadata__", None),
            contract=contract or contract_of(func),
            group=group or getattr(func, "__tool_group__", None),
            policy=getattr(func, "__tool_policy__", None),
            # Whether a session should pay for a tool only when it reaches for it is known
            # by the source of the tool, as MCP knows it, and not by the registry.
            defer=bool(defer or getattr(func, "__tool_defer__", False)),
            catalog=getattr(func, "__tool_catalog__", None),
            live_schema=live if callable(live) else None,
            # The builder that closed over a session is the one that knows it did.
            session_bound=bool(session_bound or getattr(func, "__tool_session_bound__", False)),
        )
        self._tools[name] = spec
        return spec

    def register_all(
        self, funcs: list[Callable[..., Any]], *, group: Optional[str] = None, session_bound: bool = False
    ) -> None:
        for func in funcs:
            self.register(func, group=group, session_bound=session_bound)

    def sync_group(
        self, group: str, funcs: list[Callable[..., Any]]
    ) -> tuple[list[str], list[str], list[str]]:
        """Make `group`'s tools exactly `funcs`, and return (added, removed, refused).

        The engine reads `schemas()` fresh on every turn, so replacing a group between turns is all it
        takes for the model to see a source appear or go, with no rebuild and no lost conversation.

        A name already held by a different group, or by an ungrouped built-in, is refused and not
        overwritten. Group members can be defined remotely: an MCP server that named a tool
        `write_file` would otherwise shadow the local one, and shadowing a built-in is not something
        a remote peer gets to do by choosing a name."""
        if not group:
            raise ValueError("sync_group needs a group name")
        incoming: dict[str, Callable[..., Any]] = {}
        for func in funcs:
            name = getattr(func, "__name__", None)
            if not name:
                raise ValueError("Tool function must have a __name__.")
            incoming[name] = func

        held = {name for name, spec in self._tools.items() if spec.group == group}
        refused = sorted(name for name in incoming if name not in held and name in self._tools)
        for name in refused:
            incoming.pop(name, None)

        removed = sorted(held - set(incoming))
        for name in removed:
            del self._tools[name]
        added = sorted(name for name in incoming if name not in held)
        for name, func in incoming.items():
            self.register(func, group=group)
        return added, removed, refused

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def restore(self, spec: ToolSpec) -> None:
        """Put back a spec taken off with `get` and `remove` (the model-driven edit surface,
        `edit_surface.py`), unchanged: its schema, contract and policy travel with it."""
        self._tools[spec.name] = spec

    def remove(self, name: str) -> None:
        """Drop one tool. Subagent construction uses it: a child's permission ruleset removes tools
        (`task_agents.disabled_tools`), so the tool list is decided before a request is built."""
        self._tools.pop(name, None)

    def filtered(self, *, exclude: Iterable[str] = (), session_bound: bool = True):
        """A copy of this registry without `exclude`, for a second agent to hold.

        A child build needs a registry object of its own, since an engine owns one. This is how that
        is expressed without touching the parent's: the same `ToolSpec`s, with the same function,
        schema, contract, metadata, group and policy objects, minus the names excluded.

        `session_bound=False` also leaves behind every tool whose closure holds this session's state.
        In a second session's registry such a tool is the other session acting on this
        one: a subagent's `sleep_until` once scheduled its parent's wake."""
        excluded = set(exclude)
        child = type(self)()
        child._tools = {
            name: spec
            for name, spec in self._tools.items()
            if name not in excluded and (session_bound or not spec.session_bound)
        }
        # The discovery mode travels with the copy, unlocked. The child's build rebinds the bridge closures
        # to its own registry (agent.build_engine), because a child's `tool_search` must surface only
        # what that child can call.
        child.progressive_discovery = self.progressive_discovery
        child._progressive_locked = False
        # What the parent loaded, as far as the child can call it.
        child.loaded = {name for name in self.loaded if name in child._tools}
        return child
