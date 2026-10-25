"""Which Python a workspace's code runs on — told to pyright, so its imports resolve.

pyright was started with no interpreter, so it analysed against whatever `python` it found
on PATH: not the project's venv, and without the project's packages. Every third-party
import then read `Import "x" could not be resolved`, and the post-edit note
carried that error after every edit to a file that had one — session b3a8491e-9db
(2026-09-28): eleven edits, the same `sqlalchemy` line each time. The error was the
server's blindness, not the code.

pyright asks for its settings through `workspace/configuration` (section `python`) and
reads `pythonPath` there; this module finds the value. A project config (`pyrightconfig.
json`, `[tool.pyright]`) still wins inside pyright, as it does in an editor.

Where the venv is looked for, in order: `.venv` / `venv` at the workspace root, then the
same names one folder down (`backend/.venv` in a repo whose Python lives in `backend/`) —
used only when exactly ONE such folder has a venv, since picking one of several would be
a guess. A folder counts as a venv only with its `pyvenv.cfg`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_VENV_NAMES = (".venv", "venv")
#: Folders one level down that never hold a project's own interpreter.
_SKIP = frozenset({"node_modules", "dist", "build", "__pycache__", "site-packages"})


def _venv_python(venv: Path) -> Optional[Path]:
    try:
        if not (venv / "pyvenv.cfg").is_file():
            return None
        for relative in (("Scripts", "python.exe"), ("bin", "python")):
            candidate = venv.joinpath(*relative)
            if candidate.is_file():
                return candidate
    except OSError:
        return None
    return None


def _venv_in(folder: Path) -> Optional[Path]:
    for name in _VENV_NAMES:
        found = _venv_python(folder / name)
        if found is not None:
            return found
    return None


def project_interpreter(workspace: str | Path) -> Optional[Path]:
    """The workspace's own Python, or None when there is none (or more than one)."""
    root = Path(workspace)
    found = _venv_in(root)
    if found is not None:
        return found
    try:
        folders = sorted(
            child
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".") and child.name not in _SKIP
        )
    except OSError:
        return None
    below = [found for found in map(_venv_in, folders) if found is not None]
    return below[0] if len(below) == 1 else None


def python_settings(workspace: str | Path) -> dict:
    """The `workspace/configuration` answer for pyright: `{"python": {"pythonPath": …}}`,
    or nothing when the workspace has no interpreter of its own."""
    interpreter = project_interpreter(workspace)
    return {"python": {"pythonPath": str(interpreter)}} if interpreter else {}
