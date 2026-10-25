"""What of the registry the model is offered: progressive discovery, and the schemas."""

from __future__ import annotations

from typing import Any, Optional

from ..contract import ToolContract
from ..discovery_schemas import TOOL_SEARCH


class ExposureMixin:
    def load(self, name: str) -> bool:
        """Make a deferred tool callable by its own name for the rest of this registry's life. True
        when it was newly loaded. Only a registered deferred tool under progressive discovery loads.

        Its schema stays off the wire: the model calls it from what `tool_describe` put
        in the conversation, so the tool list keeps the bytes the session opened with."""
        spec = self._tools.get(name)
        if not self.progressive_discovery or spec is None or not spec.defer:
            return False
        if name in self.loaded:
            return False
        self.loaded.add(name)
        return True

    def set_progressive_discovery(self, enabled: bool) -> None:
        """Turn progressive tool discovery on or off, once, for this registry's life.

        Raises:
            ValueError: on a second call."""
        if self._progressive_locked:
            raise ValueError(
                f"progressive discovery is already fixed at {self.progressive_discovery!r}; "
                "it is part of the cached request prefix and cannot change "
                "(build a new engine instead)"
            )
        self.progressive_discovery = bool(enabled)
        self._progressive_locked = True

    def mark_deferred(self, names: Any) -> list[str]:
        """Flag registered tools as deferrable, and return the names flagged. It only works
        before the mode locks, because the tool list is part of the cached prefix."""
        if self._progressive_locked:
            return []
        flagged: list[str] = []
        for name in names or ():
            spec = self._tools.get(name)
            if spec is not None and not spec.defer:
                spec.defer = True
                flagged.append(name)
        self.named_deferred.update(flagged)
        return flagged

    def deferred_names(self) -> list[str]:
        """The names whose schemas leave the wire under progressive discovery."""
        return [name for name, spec in self._tools.items() if spec.defer]

    def directly_callable(self) -> frozenset[str]:
        """The names the model may call directly.

        Under progressive discovery that is every registered tool except the deferred ones not yet
        loaded, which go through the `tool_call` bridge or `tool_describe`. The engine
        enforces it. A loaded tool is announced by its describe result, not the tool
        list, so this set is larger than the wire by exactly the loaded tools.

        Deferral holds only while the bridge is registered. A deferred tool is one the
        model can get back, and with no `tool_search` it is a tool nothing can offer. A denial-first
        subagent's copy was exactly that: `*: deny` removed the bridge and kept the allowed tools,
        so `verify` was told to use `git_diff` and never offered it (13 of 13 real children). A
        registry with no bridge defers nothing."""
        if self.progressive_discovery and TOOL_SEARCH in self._tools:
            return frozenset(
                name for name, spec in self._tools.items() if not spec.defer or name in self.loaded
            )
        return frozenset(self._tools)

    def _on_wire(self) -> frozenset[str]:
        """The names whose schemas the request carries: every tool, or under progressive discovery
        every tool that is not deferred. A loaded tool is callable but not listed."""
        if self.progressive_discovery and TOOL_SEARCH in self._tools:
            return frozenset(name for name, spec in self._tools.items() if not spec.defer)
        return frozenset(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """The model-facing tool list. Only `spec.schema` is ever sent: a contract's `render`,
        presenters and `timeout_ms` are runtime concerns and must never reach a provider request.

        Under progressive discovery the deferred schemas are replaced by the bridge tools (ADR
        0048)."""
        wire = self._on_wire()
        return [spec.wire_schema() for name, spec in self._tools.items() if name in wire]

    def all_schemas(self) -> list[dict[str, Any]]:
        """Every registered schema, deferred or not: what a `run_code` program may call is read
        from it (`tool_signatures`)."""
        return [spec.wire_schema() for spec in self._tools.values()]

    def schemas_by_group(self) -> list[tuple[Optional[str], dict[str, Any]]]:
        """`(group, schema)` for each tool on the wire, in registration order. The context panel
        splits external tools (MCP, the only synced group today) from built-ins by this tag. The
        schema is the one `schemas()` sends, so a deferred tool stops being counted and billed in the
        panel exactly when it leaves the request."""
        wire = self._on_wire()
        return [
            (spec.group, spec.wire_schema()) for name, spec in self._tools.items() if name in wire
        ]

    def contract(self, name: str) -> Optional[ToolContract]:
        """The registered tool's contract, or None (an unknown tool included)."""
        spec = self._tools.get(name)
        return spec.contract if spec else None

    def argument_problem(self, name: str, arguments: Optional[dict[str, Any]]) -> Optional[str]:
        """Why `arguments` cannot be passed to this tool, or None (registry_args.py)."""
        from ..registry_args import argument_problem

        return argument_problem(self._tools.get(name), name, arguments)

    def execute(self, name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"Tool not registered: {name}")
        return spec.func(**(arguments or {}))
