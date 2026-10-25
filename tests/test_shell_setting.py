"""The default shell in Settings (amendment of 2026-10-14): the person picks among the
shells this machine has, `cmd` is never one of them, and the choice survives a restart."""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.tools import shell_kinds
from harness.tools.shell_kinds import BASH, CMD, POWERSHELL, Shell

MACHINE = {
    POWERSHELL: Shell(POWERSHELL, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "PowerShell 5.1"),
    BASH: Shell(BASH, r"C:\Program Files\Git\bin\bash.exe", "Git Bash"),
    CMD: Shell(CMD, r"C:\Windows\System32\cmd.exe", "cmd"),
}


def test_the_shell_is_chosen_among_the_machines_and_kept_across_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_kinds, "local_shells", lambda: MACHINE)
    with TestClient(create_app(SessionManager(data_dir=tmp_path / "data"))) as client:
        settings = client.get("/v1/settings").json()
        assert settings["shell"] == ""
        assert [s["kind"] for s in settings["shells"]] == [POWERSHELL, BASH]  # no cmd

        assert client.post("/v1/settings/shell", json={"shell": "bash"}).json() == {"ok": True, "shell": BASH}
        assert shell_kinds.pick_default(MACHINE, env={}) == BASH
        refused = client.post("/v1/settings/shell", json={"shell": "cmd"}).json()
        assert refused["ok"] is False and shell_kinds.preferred() == BASH

    shell_kinds.set_preferred("")
    with TestClient(create_app(SessionManager(data_dir=tmp_path / "data"))) as client:
        assert client.get("/v1/settings").json()["shell"] == BASH
        assert client.post("/v1/settings/shell", json={"shell": ""}).json() == {"ok": True, "shell": ""}
