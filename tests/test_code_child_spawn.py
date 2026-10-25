"""The code-child spawn resolution (code_mode.child_command) — the fix for the frozen
sidecar, where `sys.executable` is reeve-server.exe and `python -u -m` died in the
server's own argparse (measured in production, session 3c33bb70's stderr tail).

Pins: the dev path is unchanged (python -u -m), the REEVE_CODE_CHILD override wins,
frozen mode targets the sibling reeve-code-child exe with NO interpreter flags, and a
missing sibling fails LOUD with the remedy instead of an ambiguous spawn error.
"""

from __future__ import annotations

import sys

import pytest

from harness.tools.code_mode import child_command


def test_dev_path_is_python_dash_m(monkeypatch):
    monkeypatch.delenv("REEVE_CODE_CHILD", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert child_command() == [sys.executable, "-u", "-m", "harness.tools.code_child"]


def test_the_override_env_wins_over_everything(monkeypatch, tmp_path):
    fake = tmp_path / "weird-python"
    fake.write_text("")
    monkeypatch.setenv("REEVE_CODE_CHILD", f'"{fake}" --isolated')
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert child_command() == [str(fake), "--isolated"]


def test_frozen_mode_spawns_the_sibling_exe_with_no_interpreter_flags(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("REEVE_CODE_CHILD", raising=False)
    server = tmp_path / ("reeve-server.exe" if sys.platform == "win32" else "reeve-server")
    server.write_text("")
    sibling_name = "reeve-code-child.exe" if sys.platform == "win32" else "reeve-code-child"
    (tmp_path / sibling_name).write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(server))

    argv = child_command()
    assert argv == [str(tmp_path / sibling_name)], "no -u/-m flags may ride along"
    assert all(not a.startswith("-") for a in argv[1:])


def test_frozen_without_the_sibling_fails_loud_with_the_remedy(monkeypatch, tmp_path):
    monkeypatch.delenv("REEVE_CODE_CHILD", raising=False)
    server = tmp_path / ("reeve-server.exe" if sys.platform == "win32" else "reeve-server")
    server.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(server))

    with pytest.raises(RuntimeError) as excinfo:
        child_command()
    message = str(excinfo.value)
    assert "reeve-code-child" in message
    assert "REEVE_CODE_CHILD" in message, "the dev-side escape hatch must be named"
