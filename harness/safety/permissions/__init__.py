"""Permissions: whether a proposed tool call is allowed, denied or put to the person.

`engine` decides, `guards` hold the floors that run first, `shell_rules` and `domains` are the
allowances a command or a destination can earn, and `modes` has the presets and the decision type.
The names other components import are re-exported here, together with the reach geometry and risk
classes the engine is built on.
"""

from __future__ import annotations

from ..boundary import Boundary, command_text, protected_paths, read_paths, write_paths
from ..risk import (
    WEB_RESEARCH_TOOLS,
    WRITE_TOOLS,
    RiskClass,
    RiskOverrides,
    classify,
    is_consequential,
)
from .engine import PermissionEngine, standing_rule_candidate
from .guards import DEFAULT_GUARDS, PERSISTENT_AUTHORITY_TOOLS, GuardFn, GuardVerdict, persistent_authority
from .modes import Decision, Mode
from .shell_rules import split_commands

__all__ = [
    "Boundary", "DEFAULT_GUARDS", "Decision", "GuardFn", "GuardVerdict", "Mode",
    "PERSISTENT_AUTHORITY_TOOLS", "PermissionEngine", "RiskClass", "RiskOverrides",
    "WEB_RESEARCH_TOOLS", "WRITE_TOOLS", "classify", "command_text", "is_consequential",
    "persistent_authority", "protected_paths", "read_paths", "split_commands",
    "standing_rule_candidate", "write_paths",
]
