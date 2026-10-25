"""The engine's world swap for bound sessions.

A session with an `environment_id` builds a `RemoteWorld` — phase 1's compatibility
probe runs first (`verify=True`), so an unreachable or unsupported host fails the
session start with the typed error — and every local side store that would write a
controller-side spelling of the remote path is absent. A session with no binding builds
the byte-identical `LocalWorld`. No test spawns ssh: the store's runner seam holds a
`FakeSsh`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.orchestration.remote_env import RemoteAuthError, RemoteExecutor, RemoteWorld
from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.interface.server import SessionManager
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh
from harness.orchestration.worlds import LocalWorld

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
REMOTE = "/home/dev/project"


def _bound_manager(tmp_path, runner):
    host = FakeRemoteHost(directories={REMOTE})
    # Only attach the in-memory host to a runner that is meant to answer the probe
    # successfully (canned failures must keep their own stdout/stderr answers).
    if (
        isinstance(runner, FakeSsh)
        and runner.host is None
        and runner.stdout is None
        and runner.returncode == 0
        and runner.platform == "Linux"
    ):
        runner.host = host
    store = RemoteEnvironmentStore(tmp_path / "remote-environments.json", runner=runner)
    manager = SessionManager(workspace=tmp_path)
    manager.remote_environments = store
    profile = store.create(PROFILE)
    result = manager.set_session_environment("sess-r", profile["id"], REMOTE)
    assert result["ok"], result
    return manager, store, profile


def test_bound_session_builds_the_remote_world(tmp_path):
    fake = FakeSsh()
    manager, _store, _profile = _bound_manager(tmp_path, fake)

    engine = manager.get_engine("sess-r")
    assert engine is not None
    assert isinstance(engine.world, RemoteWorld)
    assert engine.world.root == REMOTE
    assert isinstance(engine.executor, RemoteExecutor)
    assert engine.roots and engine.roots[0]["path"] == REMOTE

    # The phase-1 compatibility probe ran before the world existed.
    assert any("uname -s" in call[-1] for call in fake.calls)
    assert fake.last_destination == "dev@dev.example.com"

    output = engine.executor.run("pwd")
    assert output["exit_code"] == 0
    assert output["output"].strip() == REMOTE


def test_unbound_session_still_builds_the_local_world(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    engine = manager.get_engine("local-1")
    assert isinstance(engine.world, LocalWorld)
    assert engine.world.root == tmp_path.resolve()
    assert not getattr(engine.world, "is_remote", False)


def test_unreachable_host_fails_the_session_start_with_the_typed_error(tmp_path):
    manager, _store, _profile = _bound_manager(tmp_path, FakeSsh.auth_failure())
    with pytest.raises(RemoteAuthError) as excinfo:
        manager.get_engine("sess-r")
    assert "Authentication failed" in str(excinfo.value)
    assert "sess-r" not in manager._engines


def test_unsupported_host_fails_the_session_start(tmp_path):
    manager, _store, _profile = _bound_manager(tmp_path, FakeSsh.unsupported())
    with pytest.raises(Exception) as excinfo:
        manager.get_engine("sess-r")
    assert "FreeBSD" in str(excinfo.value)


def test_permissions_scope_writes_to_the_remote_workspace(tmp_path):
    manager, _store, _profile = _bound_manager(tmp_path, FakeSsh())
    engine = manager.get_engine("sess-r")
    permissions = engine.permissions

    # Inside the remote anchor: the boundary allows it (workspace-write then asks).
    inside = permissions.evaluate("write_file", {"path": "notes.txt"})
    assert inside.reason != "path is not in a writable directory: notes.txt"
    assert inside.needs_user is True

    # Outside every root: an ordinary ask in `workspace-write`, and the card
    # is told WHICH path fell outside the remote roots.
    outside = permissions.evaluate("write_file", {"path": "/etc/evil.txt"})
    assert outside.allowed is False and outside.needs_user is True
    assert "not in a writable directory" in outside.reason


def test_remote_sessions_have_no_local_side_stores(tmp_path):
    """Spill/plan/undo/intents write on the controller; a remote world's locator would
    point at a path that `read_file` resolves on the wrong machine."""
    manager, _store, _profile = _bound_manager(tmp_path, FakeSsh())
    engine = manager.get_engine("sess-r")
    assert engine.spill is None
    assert getattr(engine, "plan_path", None) is None
    assert getattr(engine, "undo", None) is None
    assert getattr(engine, "intents", None) is None

    # A local spelling of the remote root was never created on this disk.
    assert not Path(REMOTE).exists()


def test_remote_session_context_names_the_host_and_the_anchor(tmp_path):
    manager, _store, _profile = _bound_manager(tmp_path, FakeSsh())
    engine = manager.get_engine("sess-r")
    prompt = engine.messages[0]["content"]
    assert "Remote environment" in prompt
    assert "dev.example.com" in prompt
    assert REMOTE in prompt
    assert "$REEVE_SCRATCH" in prompt
