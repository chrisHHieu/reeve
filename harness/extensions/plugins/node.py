"""A plugin's Node packages (design §8.5).

A plugin whose hooks or servers are Node programs usually ships a `package.json` and
expects its packages installed. Reeve installs them only when three things hold: the
plugin runs code at all (packages nothing runs are not installed), it has a `package.json`,
and it has a lockfile that pins exactly what gets installed — `bun.lock`/`bun.lockb`
(`bun install --frozen-lockfile`) or `npm-shrinkwrap.json`/`package-lock.json`
(`npm ci`). Always with `--ignore-scripts`: no package's install script runs. yarn and
pnpm lockfiles, or none, are not installed and the consent screen says so.

`package.json` and the lockfile are part of the executable digest (`executable.py`), so
the person agrees to the pinned set, and a changed lockfile needs their yes again. The
install runs once, into the installed copy, after that yes; a failure or a timeout leaves
a warning on the install and never blocks it. Plugins never get Python packages installed
this way — a plugin that needs them installs them into `${CLAUDE_PLUGIN_DATA}` itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Optional

TIMEOUT_SECONDS = 60.0

#: (lockfile, package manager, argv after the program) — first match wins.
_LOCKFILES = (
    ("bun.lock", "bun", ["install", "--frozen-lockfile", "--ignore-scripts"]),
    ("bun.lockb", "bun", ["install", "--frozen-lockfile", "--ignore-scripts"]),
    ("npm-shrinkwrap.json", "npm", ["ci", "--ignore-scripts"]),
    ("package-lock.json", "npm", ["ci", "--ignore-scripts"]),
)
_UNSUPPORTED = ("yarn.lock", "pnpm-lock.yaml")


def node_setup(root: Path) -> Optional[dict[str, Any]]:
    """What installing this plugin's packages would take: `{manager, argv, lockfile}`, or
    `{skip: reason}` when they cannot be installed, or None when there are none."""
    root = Path(root)
    if not (root / "package.json").is_file():
        return None
    for lockfile, manager, args in _LOCKFILES:
        if (root / lockfile).is_file():
            return {"manager": manager, "argv": [manager, *args], "lockfile": lockfile}
    for lockfile in _UNSUPPORTED:
        if (root / lockfile).is_file():
            return {"skip": f"its Node packages are pinned by {lockfile}, which Reeve does not install — install them yourself"}
    return {"skip": "it has a package.json but no lockfile, so its Node packages are not installed"}


def install_node_packages(
    root: Path, *, run: Optional[Callable[..., Any]] = None, which: Optional[Callable[[str], Optional[str]]] = None
) -> str:
    """Install the packages `node_setup` names into `root`. Returns a warning, or '' when
    they installed (or there was nothing to install). Never raises."""
    setup = node_setup(root)
    if setup is None:
        return ""
    if "skip" in setup:
        return str(setup["skip"])
    program = (which or shutil.which)(setup["manager"])
    if program is None:
        return f"{setup['manager']} is not installed, so its Node packages were not installed"
    if run is None:
        from harness.platform.spawn import run
    try:
        done = run(
            [program, *setup["argv"][1:]],
            cwd=str(root),
            timeout=TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # a timeout, a missing file — a warning, not a failed install
        return f"installing its Node packages failed: {type(exc).__name__}"
    if getattr(done, "returncode", 1) != 0:
        tail = (getattr(done, "stderr", "") or "").strip().splitlines()[-1:] or [""]
        return f"installing its Node packages failed (exit {done.returncode}) {tail[0][:200]}".rstrip()
    return ""
