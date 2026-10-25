"""The tool registry: the callables the runtime owns, as JSON schemas for the model plus execution.

Permission checks live in the `PermissionEngine` and are applied by the turn engine, not here.
Schema generation is reused from aisuite (`Tools`), so the extraction of a schema from a docstring and
type hints is not reimplemented.
"""

from __future__ import annotations

from .exposure import ExposureMixin
from .registration import RegistrationMixin
from .spec import ToolSpec

__all__ = ["ToolRegistry", "ToolSpec"]


class ToolRegistry(RegistrationMixin, ExposureMixin):
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        # Progressive tool discovery. When on, tools whose spec declares `defer` are
        # replaced on the wire by the `tool_search`, `tool_describe` and `tool_call` bridge, which
        # `build_engine` registers. It is fixed for the registry's life: the tool set is part of the
        # cached request prefix, and a second declaration is refused rather than merged.
        self.progressive_discovery = False
        self._progressive_locked = False
        # Built-ins deferred by name (`mark_deferred`): off the wire, callable by name.
        self.named_deferred: set[str] = set()
        # Deferred tools the model has loaded (`load`): callable by their own names, with
        # their schemas left off the wire, so a load never changes the cached prefix.
        # The set only grows.
        self.loaded: set[str] = set()
