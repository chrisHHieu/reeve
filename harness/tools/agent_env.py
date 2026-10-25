"""The environment an agent's own process starts with — without Reeve's Python.

Reeve is a Python program. When it is launched from a shell that activated its venv (the
dev server, a terminal in the repo), that activation is in `os.environ`: `VIRTUAL_ENV`
names Reeve's venv and its `Scripts`/`bin` folder sits on `PATH`. Every shell, terminal
and pty the agent opened inherited both, so in someone else's project `python`, `pip` and
`uv` aimed at the environment Reeve itself runs from. Session b3a8491e-9db (2026-09-28)
got `warning: VIRTUAL_ENV=d:\\Project\\reeve\\.venv does not match the project
environment` from every `uv sync` and had to blank the variable by hand; a plain
`pip install` there would have written into Reeve's own venv.

Only Reeve's OWN environment is removed (the one `sys.prefix` names, and only when that is
a venv): an environment the person activated for their project is theirs to keep.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping

#: What an activation script sets besides VIRTUAL_ENV (venv's activate / Activate.ps1).
_ACTIVATION_KEYS = (
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "_OLD_VIRTUAL_PATH",
    "_OLD_VIRTUAL_PYTHONHOME",
    "_OLD_VIRTUAL_PROMPT",
)


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def without_own_python(
    env: Mapping[str, str],
    *,
    prefix: str | None = None,
    base_prefix: str | None = None,
) -> dict[str, str]:
    """`env` minus Reeve's own venv activation. `prefix`/`base_prefix` default to this
    interpreter's; tests pass their own."""
    prefix = sys.prefix if prefix is None else prefix
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    out = dict(env)
    if _same_path(prefix, base_prefix):
        return out  # not a venv (a frozen build, a system Python): nothing of ours to remove
    if out.get("VIRTUAL_ENV") and _same_path(out["VIRTUAL_ENV"], prefix):
        for key in _ACTIVATION_KEYS:
            out.pop(key, None)
    scripts = os.path.join(prefix, "Scripts" if os.name == "nt" else "bin")
    for key in [k for k in out if k.upper() == "PATH"]:
        kept = [p for p in out[key].split(os.pathsep) if p and not _same_path(p, scripts)]
        out[key] = os.pathsep.join(kept)
    return out
