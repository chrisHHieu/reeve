"""The standard library the frozen `reeve-code-child` carries: one list, read by the spec and
by the check that proves it.

A `run_code` program imports whatever the model writes. PyInstaller puts into each exe's
archive only the modules its analysis can reach from that exe's entry, and the child's entry
is `harness.tools.code_child`, which imports almost nothing. So the child carried whatever
happened to be reachable: the build of 2026-10-11 (`build/reeve-server/PYZ-02.toc`) had
`csv` and `statistics` but not `sqlite3`, and a program importing it would have failed in the
installed app while passing in dev, where the child is the venv's own python. The rule is now
the whole standard library, minus what a program must not have or cannot use.
"""

from __future__ import annotations

import importlib.util
import sys

#: Left out on purpose. GUI and IDE (`tkinter`, `turtle`, `turtledemo`, `idlelib`: the spec
#: already excludes tkinter), the test suite and build tooling (`test`, `lib2to3`, `ensurepip`,
#: `venv`, `pydoc_data`, `msilib`), and two modules with an effect on import: `antigravity`
#: opens a web browser and `this` prints.
EXCLUDED = frozenset(
    {
        "antigravity",
        "ensurepip",
        "idlelib",
        "lib2to3",
        "msilib",
        "pydoc_data",
        "test",
        "this",
        "tkinter",
        "turtle",
        "turtledemo",
        "venv",
    }
)


def top_level() -> list[str]:
    """Every public standard-library module this interpreter can import, minus `EXCLUDED`.

    A private module (`_csv`, `_sqlite3`) comes in with its public one. A module this platform
    does not have (`fcntl` on Windows, `msvcrt` elsewhere) is not listed: it cannot be frozen
    where it does not exist."""
    return [
        name
        for name in sorted(sys.stdlib_module_names)
        if not name.startswith("_")
        and name not in EXCLUDED
        and importlib.util.find_spec(name) is not None
    ]


def is_test_module(name: str) -> bool:
    """A package's own tests (`ctypes.test`, `idlelib.idle_test`, `tkinter.test`), never wanted."""
    return any(part in ("test", "tests", "idle_test") for part in name.split("."))
