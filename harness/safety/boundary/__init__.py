"""The reach axis: what a proposed action can touch, kept apart from whether anyone consents.

Approval answers "did a person agree?" and the boundary answers "what can the action reach?". An
approval dialog is not a filesystem boundary, and a sandbox does not stand in for the person's
intent, so the two answers never pass for each other. When one `evaluate` decided both, the mode
chose whether to ask and where the agent could reach, with the path geometry interleaved with the
allow-lists.

The geometry lives here, as one object that answers the reach questions: which roots are writable,
which files are protected, and where a write tool's paths land. The engine's boundary guards consult
it. A remote-sandbox provider could implement the same geometry against its own world and every reach
check would move with it, while the approval axis (modes, allow-lists, session grants) never notices.

Everything here is pure geometry over arguments and roots: no mode, no allow-list, no judgement.
Nothing that answers a question of consent belongs here.
"""

from __future__ import annotations

from .geometry import Boundary
from .protected import is_protected_in_project, protected_dirs, protected_paths, secret_paths
from .spelling import normalise, paths_overlap
from .tool_paths import command_text, read_paths, reads_files, write_paths

__all__ = [
    "Boundary", "command_text", "is_protected_in_project", "normalise", "paths_overlap",
    "protected_dirs", "protected_paths", "read_paths", "reads_files", "secret_paths", "write_paths",
]
