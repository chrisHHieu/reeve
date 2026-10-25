"""`grep` and `list_files` run the ripgrep an install carries, whatever the machine has.

Windows and macOS ship no ripgrep, so an installed Reeve searched with the Python walk unless the
person had installed `rg` themselves: 12.7 s against 0.2 s over this repository, with binaries read
and ignored files searched (measured 2026-10-11). The installers now stage one next to the sidecar
binary (`sidecar/rg/rg[.exe]`), and these tests pin that it is the one that runs: with nothing on
PATH, and in preference to whatever PATH holds.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from harness.tools import grep_engines
from harness.tools.listing import list_files_tools
from harness.tools.search import search_tools

RG_NAME = "rg.exe" if sys.platform == "win32" else "rg"
needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


def _install(tmp_path: Path, monkeypatch) -> Path:
    """A sidecar folder shaped like an install: the server binary with `rg/` beside it."""
    sidecar = tmp_path / "sidecar"
    (sidecar / "rg").mkdir(parents=True)
    server = sidecar / ("reeve-server.exe" if sys.platform == "win32" else "reeve-server")
    server.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(server))
    return sidecar / "rg" / RG_NAME


def test_a_dev_checkout_carries_none(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)  # the folder, with no binary in it
    assert grep_engines.carried_rg() is None


def test_the_carried_one_is_found_next_to_the_server_binary(tmp_path, monkeypatch):
    carried = _install(tmp_path, monkeypatch)
    carried.write_bytes(b"")
    assert grep_engines.carried_rg() == str(carried.resolve())


@needs_rg
def test_grep_and_list_files_run_it_with_nothing_on_path(tmp_path, monkeypatch):
    real = shutil.which("rg")
    carried = _install(tmp_path, monkeypatch)
    shutil.copy2(real, carried)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("the needle\n", encoding="utf-8")
    # Only ripgrep reads `.gitignore`; the Python walk would list and search this file.
    (workspace / ".gitignore").write_text("skipped.txt\n", encoding="utf-8")
    (workspace / "skipped.txt").write_text("the needle\n", encoding="utf-8")

    found = search_tools(str(workspace))[0](pattern="needle")
    assert found["engine"] == "ripgrep"
    assert [match["file"] for match in found["matches"]] == ["a.txt"]

    listed = list_files_tools(str(workspace))[0]()
    assert sorted(listed["files"]) == [".gitignore", "a.txt"]


def test_it_comes_before_the_one_on_path(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    carried = _install(tmp_path, monkeypatch)
    carried.write_bytes(b"")
    monkeypatch.setattr(shutil, "which", lambda _name: "rg-from-path")
    commands = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd) or SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()

    search_tools(str(workspace))[0](pattern="needle")
    list_files_tools(str(workspace))[0]()

    assert [cmd[0] for cmd in commands] == [str(carried.resolve())] * 2
