"""Reeve's own venv never reaches the agent's shells (harness/tools/agent_env.py).

Session b3a8491e-9db (2026-09-28): the server was started from a shell with Reeve's venv
activated, so every agent shell carried `VIRTUAL_ENV=<reeve>\\.venv` — uv warned on each
`uv sync` in the user's project, and the model had to blank the variable by hand. Only
Reeve's own environment is removed; the person's is theirs.
"""

from __future__ import annotations

import os
import sys

from harness.tools.agent_env import without_own_python


def _layout(tmp_path):
    own = tmp_path / "reeve" / ".venv"
    scripts = own / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    base = tmp_path / "python"
    base.mkdir()
    theirs = tmp_path / "project" / ".venv"
    (theirs / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    return own, scripts, base, theirs


def test_reeves_own_activation_is_removed(tmp_path):
    own, scripts, base, _ = _layout(tmp_path)
    env = {
        "VIRTUAL_ENV": str(own).lower() if os.name == "nt" else str(own),  # "d:\..." as seen
        "VIRTUAL_ENV_PROMPT": "(.venv)",
        "PATH": os.pathsep.join([str(scripts), "/usr/bin", r"C:\Windows"]),
        "HOME": "/home/x",
    }
    out = without_own_python(env, prefix=str(own), base_prefix=str(base))
    assert "VIRTUAL_ENV" not in out and "VIRTUAL_ENV_PROMPT" not in out
    assert str(scripts) not in out["PATH"].split(os.pathsep)
    assert out["PATH"].split(os.pathsep) == ["/usr/bin", r"C:\Windows"]
    assert out["HOME"] == "/home/x"
    assert env["VIRTUAL_ENV"]  # the input is not mutated


def test_a_venv_the_person_activated_is_kept(tmp_path):
    own, _, base, theirs = _layout(tmp_path)
    env = {"VIRTUAL_ENV": str(theirs), "PATH": str(theirs / "Scripts")}
    out = without_own_python(env, prefix=str(own), base_prefix=str(base))
    assert out == env


def test_nothing_changes_when_reeve_is_not_in_a_venv(tmp_path):
    # A frozen build or a system interpreter: sys.prefix IS the base, and its folder on
    # PATH is the machine's Python, not ours to remove.
    base = tmp_path / "python"
    (base / "Scripts").mkdir(parents=True)
    env = {"PATH": str(base / "Scripts"), "VIRTUAL_ENV": str(base)}
    assert without_own_python(env, prefix=str(base), base_prefix=str(base)) == env


def test_the_shell_executor_starts_without_it(tmp_path, monkeypatch):
    from harness.tools.shell import LocalExecutor

    if sys.prefix == sys.base_prefix:
        return  # this interpreter is not a venv; the rule above covers the logic
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    executor = LocalExecutor(cwd=tmp_path)
    assert "VIRTUAL_ENV" not in executor._env
