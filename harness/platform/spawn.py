"""One spawn seam for every child process Reeve owns.

Owned children go through here: shell commands (foreground and background), terminal
panels, the code-mode child, the POSIX PTY, the language servers, and the short-lived
helpers (`git`, ripgrep, `taskkill`, archive tools, installers). The seam exists for the
one thing each site used to decide for itself — how a child is grouped, hidden and torn
down — and for the hook a future per-process policy (sandbox, resource cap, audit) needs:
without a single spawn point there is nowhere to put one, which is why every earlier
hardening pass listed this as the prerequisite rather than the sandbox itself.

Deliberately NOT here, so the invariant test's allowlist stays honest:

- the MCP stdio transport (the SDK spawns its own client process),
- pywinpty on Windows (the binding owns ConPTY),
- `os.startfile`/shell invocations with no explicit argv.

Conventions:

- **Process group** (`new_group=True`, the default): Windows
  `CREATE_NEW_PROCESS_GROUP`, POSIX `start_new_session`, so a stop targets the tree the
  caller owns instead of Reeve's own group.
- **No console flash** (`no_window=True`, the default): Windows children get
  `CREATE_NO_WINDOW`; a PTY or an interactive shell that needs a console asks for
  `no_window=False`.
- **Environment passes through.** Callers already resolve `REEVE_*` and secrets through
  the one env builder; a second builder here would be a second source of truth.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Mapping, Optional, Sequence

_IS_WINDOWS = os.name == "nt"


def spawn(
    argv: "Sequence[str] | str",
    *,
    cwd: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    new_group: bool = True,
    no_window: bool = True,
    **kwargs: Any,
) -> subprocess.Popen:
    """`subprocess.Popen` with the child-process conventions above.

    `**kwargs` passes through untouched (stdio, `text`, `encoding`, `bufsize`, …), so a
    routed call site keeps its exact semantics; a caller's own `creationflags` are OR-ed
    with the seam's.
    """
    flags = int(kwargs.pop("creationflags", 0) or 0)
    if _IS_WINDOWS:
        if new_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if no_window:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            kwargs["creationflags"] = flags
    elif new_group:
        kwargs.setdefault("start_new_session", True)
    # A string is a command line handed to CreateProcess exactly as built: `cmd /s /c "…"` cannot be
    # expressed as a list, because quoting one escapes the inner quotes with a backslash.
    return subprocess.Popen(argv if isinstance(argv, str) else list(argv), cwd=cwd, env=env, **kwargs)


def run(
    argv: Sequence[str],
    *,
    cwd: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    no_window: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """`subprocess.run` for a short-lived helper, with the same conventions.

    Nothing needs to signal a helper as a tree, so helpers are not grouped; they do get
    the hidden-console treatment on Windows (a desktop app should not flash a console
    every time it shells out to `git`).
    """
    flags = int(kwargs.pop("creationflags", 0) or 0)
    if _IS_WINDOWS and no_window:
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        kwargs["creationflags"] = flags
    return subprocess.run(list(argv), cwd=cwd, env=env, timeout=timeout, **kwargs)
