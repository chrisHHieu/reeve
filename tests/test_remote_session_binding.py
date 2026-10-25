"""Session ↔ remote environment binding.

The environment id is an INSERT-only column on the session record (the per-turn save
rebuilds the record and must not unbind a session), written through
`ConversationStore.set_environment`; the routes create/switch/clear it, and the manager
validates the profile and the absolute remote workspace before anything is persisted.
No test here spawns ssh: the store's runner seam holds a `FakeSsh`.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from harness.session.conversations import ConversationStore
from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.interface.server import SessionManager, create_app
from harness.session.sessions import SessionRecord
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
REMOTE = "/home/dev/project"


def _store(tmp_path, runner=None) -> RemoteEnvironmentStore:
    return RemoteEnvironmentStore(
        tmp_path / "remote-environments.json", runner=runner or FakeSsh()
    )


def _manager(tmp_path, runner=None):
    manager = SessionManager(workspace=tmp_path)
    store = _store(tmp_path, runner)
    manager.remote_environments = store
    return manager, store


# -- the record + migration tolerance ---------------------------------------------------


def test_environment_binding_survives_the_per_turn_save(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="s1", workspace="C:/ws", model="m", mode="workspace-write"
        )
    )
    assert store.load("s1").environment_id is None

    store.set_environment("s1", "env-1", REMOTE)
    record = store.load("s1")
    assert record.environment_id == "env-1" and record.workspace == REMOTE

    # The per-turn save rebuilds the record WITHOUT the binding (exactly what the
    # manager's save() does) — the INSERT-only column must survive it.
    record.environment_id = None
    record.workspace = REMOTE
    store.save(record)
    kept = store.load("s1")
    assert kept.environment_id == "env-1" and kept.workspace == REMOTE

    store.set_environment("s1", None, "C:/ws")
    cleared = store.load("s1")
    assert cleared.environment_id is None and cleared.workspace == "C:/ws"


def test_binding_can_land_before_the_first_turn(tmp_path):
    store = ConversationStore(tmp_path)
    assert store.load("fresh") is None
    assert store.set_environment("fresh", "env-1", REMOTE) is True
    record = store.load("fresh")
    assert record.environment_id == "env-1" and record.workspace == REMOTE
    # And the bare row carries no title: the first turn's snapshot must be able to
    # land (a persisted "New session" placeholder would win the upsert's COALESCE).
    assert record.title is None


def test_a_seeded_binding_row_survives_a_restart_without_a_placeholder_title(tmp_path):
    """The binding's seed row is title-less, and the startup backfill must not turn an
    empty session's display fallback into a persisted title either — the first real save
    still owns the snapshot."""
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("s1", profile["id"], REMOTE)["ok"]
    assert manager.session_store.load("s1").title is None

    # Runs the backfill over every row on the same database.
    reopened = ConversationStore(manager.session_store.base)
    assert reopened.load("s1").title is None
    reopened.save(
        SessionRecord(
            session_id="s1",
            workspace=REMOTE,
            model="m",
            mode="workspace-write",
            messages=[{"role": "user", "content": "fix the remote build"}],
        )
    )
    assert reopened.load("s1").title == "fix the remote build"


def test_a_pre_fix_binding_row_is_healed_at_restart(tmp_path):
    """Databases written before the fix carry the placeholder as a real title (the
    seed row won the upsert). The restart pass must replace it with the first-line
    snapshot instead of skipping the row as "already migrated"."""
    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="old-remote",
            workspace=REMOTE,
            model="m",
            mode="workspace-write",
            environment_id="env-1",
            messages=[{"role": "user", "content": "fix the remote build"}],
        )
    )
    with store._lock:
        store._conn.execute(
            "UPDATE sessions SET title = 'New session' WHERE session_id = 'old-remote'"
        )
        store._conn.commit()
    assert store.load("old-remote").title == "New session"

    reopened = ConversationStore(tmp_path)  # runs the backfill
    assert reopened.load("old-remote").title == "fix the remote build"


def test_an_old_row_without_the_column_reads_as_local(tmp_path):
    """A database written before `environment_id` existed must degrade to "local"."""
    db = tmp_path / "reeve.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, "
        "mode TEXT, messages TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, updated_at) "
        "VALUES ('old', 'C:/ws', 'm', 'workspace-write', CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    store = ConversationStore(tmp_path)  # runs the migration ALTERs
    record = store.load("old")
    assert record is not None
    assert record.environment_id is None
    # And a binding can still be written onto the upgraded row.
    assert store.set_environment("old", "env-9", REMOTE) is True
    assert store.load("old").environment_id == "env-9"


# -- the manager surface ----------------------------------------------------------------


def test_set_session_environment_validates_and_round_trips(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)

    unknown = manager.set_session_environment("s1", "no-such-id", REMOTE)
    assert unknown["ok"] is False and "No remote environment" in unknown["error"]

    relative = manager.set_session_environment("s1", profile["id"], "home/dev/project")
    assert relative["ok"] is False and "absolute" in relative["error"]

    missing = manager.set_session_environment("s1", profile["id"], None)
    assert missing["ok"] is False and "required" in missing["error"]

    bound = manager.set_session_environment("s1", profile["id"], REMOTE + "/")
    assert bound["ok"] is True and bound["workspace"] == REMOTE
    assert bound["remote_workspace"] == REMOTE
    assert bound["environment"]["id"] == profile["id"]

    summary = manager.session_summary("s1")
    assert summary["environment_id"] == profile["id"]
    assert summary["remote_workspace"] == REMOTE
    # The 2b-2 wire shape: id/name/user/host/port — enough for `user@host:path`
    # readouts, never identity_file.
    assert summary["environment"] == {
        "id": profile["id"],
        "name": "Build server",
        "user": "dev",
        "host": "dev.example.com",
        "port": 22,
    }
    assert "identity_file" not in summary["environment"]

    local = manager.set_session_environment("s1", None, str(tmp_path))
    assert local["ok"] is True and local["environment_id"] is None
    # The remote path half is null on Local: a surface can never append a local
    # folder to `user@host:port`.
    assert local["remote_workspace"] is None
    after = manager.session_summary("s1")
    assert after["environment_id"] is None and after["remote_workspace"] is None


def test_clear_without_a_folder_falls_back_to_the_default_workspace(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("s1", profile["id"], REMOTE)["ok"]

    cleared = manager.set_session_environment("s1", None, None)
    assert cleared["ok"] is True
    assert cleared["workspace"] == str(tmp_path)
    assert manager.session_summary("s1")["environment_id"] is None


def test_switching_evicts_the_cached_engine(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("s1", profile["id"], REMOTE)["ok"]
    first = manager.get_engine("s1")
    assert first is not None
    assert manager.get_engine("s1") is first, "cached while bound"

    assert manager.set_session_environment("s1", None, str(tmp_path))["ok"]
    assert "s1" not in manager._engines, "the old world must not survive the switch"


def test_the_turn_save_keeps_the_remote_workspace_verbatim(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("s1", profile["id"], REMOTE)["ok"]
    engine = manager.get_engine("s1")
    manager.save("s1", engine)

    record = manager.session_store.load("s1")
    assert record.environment_id == profile["id"]
    assert record.workspace == REMOTE, "realpath must not mangle a remote path"
    # A remote path is not a local recent project.
    assert REMOTE not in manager.session_store.recent_workspaces(limit=50)


# -- the HTTP surface -------------------------------------------------------------------


def _client(tmp_path, runner=None):
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    if runner is not None:
        app.state.remote_environments._runner = runner
    return TestClient(app), app, manager


def test_session_routes_bind_and_return_the_environment(tmp_path):
    client, _app, _manager = _client(
        tmp_path, runner=FakeSsh(host=FakeRemoteHost(directories={REMOTE}))
    )
    profile = client.post("/v1/remote-environments", json=PROFILE).json()["environment"]

    bound = client.patch(
        "/v1/sessions/s1",
        json={"environment_id": profile["id"], "workspace": REMOTE},
    )
    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert body["ok"] is True and body["environment_id"] == profile["id"]

    detail = client.get("/v1/sessions/s1").json()
    assert detail["ok"] is True
    assert detail["environment_id"] == profile["id"]
    assert detail["workspace"] == REMOTE
    assert detail["environment"]["name"] == "Build server"

    invalid = client.patch(
        "/v1/sessions/s1", json={"environment_id": "nope", "workspace": REMOTE}
    )
    assert invalid.status_code == 400 and invalid.json()["ok"] is False

    relative = client.patch(
        "/v1/sessions/s1",
        json={"environment_id": profile["id"], "workspace": "relative/path"},
    )
    assert relative.status_code == 400
    assert "absolute" in relative.json()["error"]

    # Choosing local clears the binding.
    cleared = client.patch(
        "/v1/sessions/s1", json={"environment_id": None, "workspace": str(tmp_path)}
    )
    assert cleared.status_code == 200
    assert cleared.json()["environment_id"] is None
    assert client.get("/v1/sessions/s1").json()["environment_id"] is None


def test_session_detail_is_graceful_for_an_unknown_session(tmp_path):
    client, _app, _manager = _client(tmp_path)
    body = client.get("/v1/sessions/missing").json()
    assert body == {"ok": False, "error": "session not found"}


def test_ready_frame_carries_the_remote_workspace(tmp_path):
    """The GUI builds `user@host:path` from this field — the host's POSIX path when
    bound, null locally. It is never inferred from the GUI's local workspace state."""
    client, _app, _manager = _client(
        tmp_path, runner=FakeSsh(host=FakeRemoteHost(directories={REMOTE}))
    )
    profile = client.post("/v1/remote-environments", json=PROFILE).json()["environment"]
    client.patch(
        "/v1/sessions/s1",
        json={"environment_id": profile["id"], "workspace": REMOTE},
    )
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        ready = ws.receive_json()
    assert ready["type"] == "ready"
    assert ready["data"]["remote_workspace"] == REMOTE

    with client.websocket_connect("/ws/session/plain-local?agent=chat") as ws:
        ready = ws.receive_json()
    assert ready["type"] == "ready"
    assert ready["data"]["remote_workspace"] is None


def test_patch_without_environment_fields_still_renames(tmp_path):
    """Regression: the new branch must not swallow the existing PATCH shapes."""
    client, _app, manager = _client(tmp_path)
    manager.session_store.save(
        SessionRecord(
            session_id="s1", workspace=str(tmp_path), model="m", mode="workspace-write"
        )
    )
    assert client.patch("/v1/sessions/s1", json={"title": "Renamed"}).json()["ok"] is True
    assert client.get("/v1/sessions/s1").json()["title"] == "Renamed"


# -- phase 2b-2: the GUI's read models + child inheritance ------------------------------


def test_sessions_list_carries_the_binding_for_the_sidebar(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create({**PROFILE, "identity_file": "/home/dev/.ssh/id_ed25519"})
    assert manager.set_session_environment("remote-1", profile["id"], REMOTE)["ok"]

    rows = {row["session_id"]: row for row in manager.list_sessions()}
    remote = rows["remote-1"]
    assert remote["environment_id"] == profile["id"]
    # The sidebar's readout path comes from `remote_workspace`, never from the local
    # workspace state (a stale local folder must not ride into `user@host:path`).
    assert remote["remote_workspace"] == REMOTE
    assert remote["environment"] == {
        "id": profile["id"],
        "name": "Build server",
        "user": "dev",
        "host": "dev.example.com",
        "port": 22,
    }
    assert "identity_file" not in remote["environment"]
    # A local session stays byte-identical: no binding, no environment row.
    manager.session_store.save(
        SessionRecord(session_id="local-1", workspace=str(tmp_path), model="m", mode="interactive")
    )
    local = {row["session_id"]: row for row in manager.list_sessions()}["local-1"]
    assert local["environment_id"] is None and local["environment"] is None
    assert local["remote_workspace"] is None


def test_a_child_session_inherits_its_parents_binding(tmp_path):
    fake = FakeSsh(host=FakeRemoteHost(directories={REMOTE}))
    manager, store = _manager(tmp_path, fake)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("parent-r", profile["id"], REMOTE)["ok"]

    child_id = manager.create_child_session("parent-r", "Investigate", "explore", "m")
    child = manager.session_store.load(child_id)
    assert child.environment_id == profile["id"], "a child works where its parent works"

    # The child's own engine build resolves the same profile and World — not a local
    # world rooted at a POSIX spelling.
    engine = manager.get_engine(child_id)
    assert engine is not None
    assert getattr(engine.world, "is_remote", False) is True
    assert engine.world.root == REMOTE


def test_a_fork_of_a_remote_session_stays_remote(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    manager.session_store.save(
        SessionRecord(
            session_id="parent-r",
            workspace=REMOTE,
            model="m",
            mode="workspace-write",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert manager.set_session_environment("parent-r", profile["id"], REMOTE)["ok"]

    forked = manager.fork_session("parent-r")
    assert forked["ok"] is True
    record = manager.session_store.load(forked["session_id"])
    assert record.environment_id == profile["id"]
    assert record.workspace == REMOTE


def test_resolve_child_remote_raises_for_a_vanished_profile(tmp_path):
    manager, store = _manager(tmp_path)
    profile = store.create(PROFILE)
    assert manager.set_session_environment("parent-r", profile["id"], REMOTE)["ok"]
    child_id = manager.create_child_session("parent-r", "Investigate", "explore", "m")
    store.delete(profile["id"])
    child = manager.session_store.load(child_id)
    with pytest.raises(ValueError) as excinfo:
        manager._resolve_child_remote(child_id, child)
    assert "no longer exists" in str(excinfo.value)
    # Local children are untouched by the resolver.
    assert manager._resolve_child_remote("local-kid", None) == (None, None, None, False)
