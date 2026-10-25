"""A session's workspace is its primary root, never wherever the shell has wandered.

The persistent shell follows every `cd` the agent runs, so its cwd is where the last
command happened to end. Reading that as "the workspace" moved the session: b3a8491e-9db
was opened on D:\\Project\\wealthlog, the agent ran `cd D:\\Project\\wealthlog\\backend;
...`, and the save, the GUI's workspace chip and the project-memory key all followed it.
The rebuilt engine then put frontend/ and docs/ outside the workspace, and the model
asked for a folder the person had already given it.

Index 0 of `engine.roots` is the primary root: the folder the session was opened on, or
the one `promote_workspace` put there.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def session_workspace(engine: Any) -> Optional[str]:
    """The session's workspace path, or None when it has none."""
    world = getattr(engine, "world", None)
    if world is not None and getattr(world, "is_remote", False):
        # A POSIX path on another host: realpath would respell it locally.
        return str(getattr(world, "root", "") or "") or None
    roots = getattr(engine, "roots", None) or []
    if roots:
        first = roots[0]
        path = first.get("path") if isinstance(first, dict) else getattr(first, "path", None)
        if path:
            return os.path.realpath(str(path))
    executor = getattr(engine, "executor", None)
    return os.path.realpath(str(executor.cwd)) if executor is not None else None
