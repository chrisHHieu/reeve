"""`request_directory` on a remote session.

A bound session's roots live on the other host, so a grant is world-aware: the requested
path is validated ON THE HOST (absolute POSIX, must be an existing directory), and an
approved grant lands as a REMOTE root in the same shared list the `RemoteFileBackend`
and the permission boundary read. Writes are only permitted through a writable grant;
the row is tagged `remote: true`, so a grant never crosses the local/remote boundary.

Everything runs over `FakeSsh`/`FakeRemoteHost` — no ssh binary, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.interface.server import SessionManager
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
REMOTE = "/home/dev/project"
GRANTED = "/home/dev/shared"
SESSION = "sess-remote-grant"


def _managed(tmp_path, *, files=None, directories=None):
    host = FakeRemoteHost(directories={REMOTE, GRANTED, *(directories or [])})
    for path, content in (files or {}).items():
        host.write(path, content)
    fake = FakeSsh(host=host)
    manager = SessionManager(workspace=tmp_path)
    store = RemoteEnvironmentStore(tmp_path / "remote-environments.json", runner=fake)
    manager.remote_environments = store
    profile = store.create(PROFILE)
    assert manager.set_session_environment(SESSION, profile["id"], REMOTE)["ok"]
    return manager, profile, fake, host


def _row(roots, path):
    return next(row for row in roots if row["path"] == path)


# -- registration + host validation ------------------------------------------------------


def test_a_remote_grant_is_validated_on_the_host(tmp_path):
    manager, _profile, _fake, _host = _managed(tmp_path)
    assert manager.get_engine(SESSION, agent="reeve") is not None

    relative = manager.add_root(SESSION, "home/dev/shared", writable=True)
    assert relative["ok"] is False and "absolute" in relative["error"]

    missing = manager.add_root(SESSION, "/home/dev/nope", writable=True)
    assert missing["ok"] is False and "not a directory" in missing["error"]

    a_file = manager.add_root(SESSION, f"{REMOTE}/readme.md", writable=True)
    assert a_file["ok"] is False and "not a directory" in a_file["error"]

    granted = manager.add_root(SESSION, GRANTED, writable=True)
    assert granted["ok"] is True
    assert _row(granted["roots"], GRANTED)["writable"] is True


def test_a_remote_session_refuses_a_local_path_and_a_local_session_refuses_a_remote_one(
    tmp_path,
):
    manager, _profile, _fake, _host = _managed(tmp_path)
    assert manager.get_engine(SESSION, agent="reeve") is not None
    windows_path = manager.add_root(SESSION, "C:\\Users\\dev\\shared", writable=True)
    assert windows_path["ok"] is False and "absolute" in windows_path["error"]

    local_id = "sess-local-grant"
    assert manager.get_engine(local_id, agent="reeve") is not None
    remote_path = manager.add_root(
        local_id, "/reeve-nonexistent-remote-root", writable=True
    )
    assert remote_path["ok"] is False and "not a directory" in remote_path["error"]


# -- containment after an approval -------------------------------------------------------


def test_an_approved_grant_allows_reads_and_writes_only_when_writable(tmp_path):
    manager, _profile, _fake, host = _managed(
        tmp_path,
        files={f"{GRANTED}/notes.txt": "remote note\n"},
        directories={f"{GRANTED}/sub"},
    )
    engine = manager.get_engine(SESSION, agent="reeve")
    assert engine is not None

    # Ungranted: the remote backend's own containment refusal.
    before = engine.registry.execute("read_file", {"path": f"{GRANTED}/notes.txt"})
    assert "escapes" in before["error"]

    read_only = manager.add_root(SESSION, GRANTED, writable=False)
    assert read_only["ok"] is True
    row = _row(read_only["roots"], GRANTED)
    assert row["writable"] is False and row["remote"] is True

    read = engine.registry.execute("read_file", {"path": f"{GRANTED}/notes.txt"})
    assert "remote note" in read["content"]

    # The write boundary agrees with the backend: not a writable root.
    with pytest.raises(PermissionError, match="read-only"):
        engine.registry.execute(
            "write_file", {"path": f"{GRANTED}/new.txt", "content": "x"}
        )
    decision = engine.permissions.evaluate(
        "write_file", {"path": f"{GRANTED}/new.txt", "content": "x"}
    )
    # The read-only mark is a floor: refused outright, not an approvable ask.
    assert decision.allowed is False and not decision.needs_user
    assert "read-only" in decision.reason

    widened = manager.add_root(SESSION, GRANTED, writable=True)
    assert widened["ok"] is True and _row(widened["roots"], GRANTED)["writable"] is True
    written = engine.registry.execute(
        "write_file", {"path": f"{GRANTED}/new.txt", "content": "hello"}
    )
    assert isinstance(written, str)
    assert host.read(f"{GRANTED}/new.txt") == b"hello"


# -- persistence across turns and revocation ---------------------------------------------


def test_a_remote_grant_survives_a_rebuild(tmp_path):
    manager, _profile, _fake, _host = _managed(
        tmp_path, files={f"{GRANTED}/notes.txt": "still here\n"}
    )
    engine = manager.get_engine(SESSION, agent="reeve")
    assert manager.add_root(SESSION, GRANTED, writable=False)["ok"]
    manager.save(SESSION, engine)

    record = manager.session_store.load(SESSION)
    persisted = [r for r in record.extra_roots if r.get("remote")]
    assert [r["path"] for r in persisted] == [GRANTED]

    manager._engines.pop(SESSION, None)
    rebuilt = manager.get_engine(SESSION, agent="reeve")
    assert rebuilt is not None
    assert _row(manager.get_roots(SESSION), GRANTED)["writable"] is False
    read = rebuilt.registry.execute("read_file", {"path": f"{GRANTED}/notes.txt"})
    assert "still here" in read["content"]


def test_a_remote_grant_can_be_revoked(tmp_path):
    manager, _profile, _fake, _host = _managed(
        tmp_path, files={f"{GRANTED}/notes.txt": "gone soon\n"}
    )
    engine = manager.get_engine(SESSION, agent="reeve")
    assert manager.add_root(SESSION, GRANTED, writable=True)["ok"]

    removed = manager.remove_root(SESSION, GRANTED)
    assert removed["ok"] is True
    assert GRANTED not in {row["path"] for row in removed["roots"]}
    refused = engine.registry.execute("read_file", {"path": f"{GRANTED}/notes.txt"})
    assert "escapes" in refused["error"]

    manager._engines.pop(SESSION, None)
    rebuilt = manager.get_engine(SESSION, agent="reeve")
    assert rebuilt is not None
    assert GRANTED not in {row["path"] for row in manager.get_roots(SESSION)}

    primary = manager.remove_root(SESSION, REMOTE)
    assert primary["ok"] is False and "primary" in primary["error"]


def test_grants_do_not_cross_the_local_remote_boundary(tmp_path):
    manager, profile, _fake, _host = _managed(tmp_path)
    assert manager.get_engine(SESSION, agent="reeve") is not None
    assert manager.add_root(SESSION, GRANTED, writable=True)["ok"]

    # Switch to a local folder: the remote-tagged grant is not adopted locally.
    assert manager.set_session_environment(SESSION, None, str(tmp_path))["ok"]
    manager._engines.pop(SESSION, None)
    assert manager.get_engine(SESSION, agent="reeve") is not None
    assert GRANTED not in {row["path"] for row in manager.get_roots(SESSION)}

    # A local grant is not adopted remotely either.
    local = tmp_path / "local-grant"
    local.mkdir()
    assert manager.add_root(SESSION, str(local), writable=True)["ok"]
    assert manager.set_session_environment(SESSION, profile["id"], REMOTE)["ok"]
    manager._engines.pop(SESSION, None)
    assert manager.get_engine(SESSION, agent="reeve") is not None
    assert str(local.resolve()) not in {row["path"] for row in manager.get_roots(SESSION)}
