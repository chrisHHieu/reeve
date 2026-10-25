"""The personal WhatsApp connector in the harness: listed like any connector since it left
experimental (2026-10-21) and still paired only after its risk is acknowledged, connected only by a
phone's link, its local server armed one per session in the session's
folder and gated by its own read-only hints, and disconnecting unlinks and deletes what was kept.
No WhatsApp: the daemon is never started here."""

from __future__ import annotations

import pytest

from harness.extensions.mcp.config import read_global
from harness.integrations.whatsapp import connector
from harness.interface.server.manager import SessionManager

NAME = connector.NAME


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    from harness.integrations.connectors.descriptors import get_descriptor

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    # Whether the optional library is installed is not what these tests are about (CI installs
    # no `whatsapp` extra); what the harness does with the connector is.
    monkeypatch.setattr(get_descriptor(NAME), "available", True)
    monkeypatch.setattr(connector, "available", lambda: True)
    return SessionManager(data_dir=tmp_path / "data")


def _row(manager):
    return next((c for c in manager.list_connectors() if c["name"] == NAME), None)


def test_it_is_listed_with_no_opt_in_and_still_says_its_risk(manager):
    row = _row(manager)
    assert row is not None and not row["experimental"] and row["auth"] == "pairing" and not row["connected"]
    assert "ban" in row["risk_notice"] and "approval" in row["risk_notice"]


def test_pairing_waits_for_the_acknowledgment(manager):
    refused = manager.whatsapp_pair(phone="+84901111111")
    assert refused["error"] == "risk acknowledgment required" and refused["risk_notice"] == connector.RISK_NOTICE
    assert manager._whatsapp_proc is None  # nothing started without the acknowledgment


def test_a_linked_phone_connects_it_and_each_session_arms_its_own_server(manager, tmp_path):
    connector.record_paired(manager.secrets, "+84909999999")
    row = _row(manager)
    assert row["connected"] and row["account"] == "+84909999999" and row["mode"] == "mcp"
    assert read_global()[NAME]["args"] == ["-m", "harness.integrations.whatsapp"]

    workspace = tmp_path / "ws"
    workspace.mkdir()
    [server] = [s for s in manager._mcp_plan("s1", str(workspace), "reeve") if s.name == NAME]
    assert server.per_session and server.cwd == str(workspace)
    assert server.approval_by_hints  # reads run, sends ask
    assert server.args == ["-m", "harness.integrations.whatsapp"]


def test_a_session_with_the_connector_off_arms_nothing(manager, tmp_path, monkeypatch):
    connector.record_paired(manager.secrets, "+84909999999")
    monkeypatch.setattr(type(manager), "effective_connectors", lambda self, sid, agent=None: set())
    assert NAME not in {s.name for s in manager._mcp_plan("s1", str(tmp_path), "reeve")}


def test_the_desktop_build_runs_its_server_and_daemon_as_subcommands_of_the_sidecar(monkeypatch):
    """A frozen executable takes no `python -m`: the server and the daemon are `whatsapp` and
    `whatsapp daemon` of reeve-server itself (packaging/server_entry.py)."""
    import importlib.util
    import sys
    from pathlib import Path

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert connector.launch() == {"command": sys.executable, "args": ["whatsapp"]}
    assert connector.daemon_command(42, "n0nce") == [sys.executable, "whatsapp", "daemon", "--parent", "42", "--nonce", "n0nce"]

    entry = Path(__file__).resolve().parent.parent / "packaging" / "server_entry.py"
    spec = importlib.util.spec_from_file_location("server_entry_probe", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # the __main__ guard keeps the server from starting
    ran = []
    monkeypatch.setattr("harness.integrations.whatsapp.daemon.main", lambda args: ran.append(("daemon", args)) or 0)
    monkeypatch.setattr("harness.integrations.whatsapp.server.main", lambda: ran.append(("server",)) or 0)
    assert module._whatsapp(["daemon", "--parent", "42"]) == 0 and module._whatsapp([]) == 0
    assert ran == [("daemon", ["--parent", "42"]), ("server",)]


def test_disconnecting_unlinks_the_device_and_deletes_what_was_kept(manager, monkeypatch):
    from harness.integrations.whatsapp.tools import account_folder

    connector.record_paired(manager.secrets, "+84909999999")
    folder = account_folder()
    folder.mkdir(parents=True)
    (folder / "messages.db").write_bytes(b"x")
    asked = []
    monkeypatch.setattr(
        "harness.integrations.whatsapp.client.request", lambda method, path, *a, **k: asked.append(path) or {"ok": True}
    )
    manager.disconnect_connector(NAME)
    assert asked == ["/logout"]
    assert not folder.exists()
    assert NAME not in read_global() and not _row(manager)["connected"]


def test_the_server_starts_nothing_for_an_account_never_linked(manager):
    assert manager.start_whatsapp() is False and manager._whatsapp_proc is None


def test_the_daemon_started_is_known_by_its_nonce_not_its_pid(manager, monkeypatch):
    """On Windows a venv's `python.exe` is a launcher whose child is the interpreter, so the
    daemon writes a pid the server never held: matching on it waited twenty seconds and said
    "did not start" while the daemon was up and showing a QR code (2026-10-09)."""
    import json

    from harness.integrations.whatsapp.daemon import INFO_FILE
    from harness.integrations.whatsapp.tools import account_folder

    class Launcher:
        pid = 111  # the launcher's pid; the interpreter's is another

        def poll(self):
            return None

    started = []

    def fake_spawn(argv, cwd=None, **_kw):
        nonce = argv[argv.index("--nonce") + 1]
        started.append(nonce)
        (account_folder() / INFO_FILE).write_text(json.dumps({"port": 1, "token": "t", "pid": 222, "nonce": nonce}))
        return Launcher()

    monkeypatch.setattr("harness.platform.spawn.spawn", fake_spawn)
    manager._whatsapp_daemon()
    manager._whatsapp_daemon()  # the same daemon answers: nothing new starts
    assert len(started) == 1


def test_the_page_reads_what_is_kept_changes_how_long_and_clears_it_without_unlinking(manager):
    import time

    from harness.integrations.whatsapp.records import Message
    from harness.integrations.whatsapp.store import DAY_S, Store
    from harness.integrations.whatsapp.tools import account_folder

    assert manager.whatsapp_kept()["messages"] == 0  # nothing linked yet: nothing kept, no file made
    assert not (account_folder() / "messages.db").exists()

    chat = "84901111111@s.whatsapp.net"
    now = time.time()
    store = Store(account_folder() / "messages.db")
    store.add([
        Message(chat=chat, id=f"m{age}", sender=chat, from_me=False, ts=int(now - age * DAY_S), kind="text", text="xin chào")
        for age in (1, 45, 200)
    ])
    store.close()

    kept = manager.whatsapp_kept()
    assert kept["messages"] == 3 and kept["keep_days"] == 90 and kept["bytes"] > 0
    assert manager.whatsapp_set_keep(7)["ok"] is False  # only the choices the page offers
    assert manager.whatsapp_set_keep(30)["messages"] == 1  # applied at once, not at the next pass
    assert manager.whatsapp_kept()["keep_days"] == 30

    connector.record_paired(manager.secrets, "+84909999999")
    assert manager.whatsapp_clear_kept()["messages"] == 0
    assert manager._whatsapp_linked()  # still linked: only the messages went
