"""SSH password auth for remote environments (password-auth update).

Three promises, pinned here without spawning a real ssh and without touching the real
Credential Manager:

- the password reaches ssh through the askpass handshake only (a 0600 helper + secret in
  the private state dir, ``SSH_ASKPASS_REQUIRE=force``, ``BatchMode=yes`` omitted), and
  never through argv, the inherited environment, or a log-bound string;
- the helper files exist exactly for the duration of the call (deleted in a ``finally``,
  including on a failed or missing ssh);
- a remembered password lives in the credential seam under a per-profile target — the
  profile JSON keeps only ``remembered``, the wire keeps only ``has_password``, and
  unchecking/deleting cleans the secret up.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.config.credentials import CredentialStoreUnavailable, UnavailableCredentialStore, WindowsCredentialStore, credential_target, default_credential_store
from harness.orchestration.remote_environments import ASKPASS_HELPER_PREFIX, RemoteEnvironmentStore, ssh_argv, test_connection
from harness.interface.server import SessionManager, create_app
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh
from harness.orchestration.worlds import remote_world

PROFILE = {
    "name": "Build server",
    "host": "dev.example.com",
    "user": "ubuntu",
    "port": 2222,
    "identity_file": "~/.ssh/id_ed25519",
}
PASSWORD = "p@ss word & <secret> | 100%"
REMOTE = "/home/dev/project"


class MemoryCredentials:
    """The credential seam as a dict — what would have gone to Credential Manager."""

    available = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def get(self, target: str):
        return self.values.get(target)

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def delete(self, target: str) -> bool:
        self.deleted.append(target)
        return self.values.pop(target, None) is not None


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    # The askpass helper must NOT land in the real user state dir during tests.
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REEVE_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.delenv("REEVE_SSH_PATH", raising=False)


def _store(tmp_path, runner=None, credentials=None) -> RemoteEnvironmentStore:
    return RemoteEnvironmentStore(
        tmp_path / "remote-environments.json",
        runner=runner or FakeSsh(),
        credentials=credentials or MemoryCredentials(),
    )


def _client(tmp_path, runner=None, credentials=None) -> TestClient:
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    if runner is not None:
        app.state.remote_environments._runner = runner
    if credentials is not None:
        app.state.remote_environments._credentials = credentials
    return TestClient(app)


def _askpass_files() -> list[Path]:
    base = Path(os.environ["REEVE_STATE_DIR"])
    if not base.is_dir():
        return []
    return sorted(base.glob(f"{ASKPASS_HELPER_PREFIX}*"))


class RecordingRunner:
    """Wraps FakeSsh and snapshots the askpass files while each call is in flight."""

    def __init__(self, inner: FakeSsh) -> None:
        self.inner = inner
        self.seen: list[dict] = []

    def __call__(self, *, args, timeout, input_text=None, env=None):
        files: dict[str, dict] = {}
        for path in _askpass_files():
            files[path.name] = {
                "mode": stat.S_IMODE(path.stat().st_mode),
                "content": path.read_text(encoding="utf-8"),
            }
        self.seen.append({"args": list(args), "env": dict(env or {}), "files": files})
        return self.inner(args=args, timeout=timeout, input_text=input_text, env=env)

    @property
    def last(self) -> dict:
        return self.seen[-1]

    def secret(self, event: dict) -> str:
        for name, info in event["files"].items():
            if name.endswith(".secret"):
                return str(info["content"])
        raise AssertionError("no askpass secret file was present during the call")


# -- the askpass mechanism --------------------------------------------------------------


def test_password_probe_uses_askpass_and_drops_batch_mode():
    runner = RecordingRunner(FakeSsh())
    result = test_connection(PROFILE, runner=runner, password=PASSWORD)

    assert result == {
        "ok": True,
        "platform": "linux",
        "arch": "x64",
        "home": "/home/dev",
        "error": None,
    }
    args, env = runner.last["args"], runner.last["env"]
    assert "BatchMode=yes" not in args, "a password must not ride the batch-posture argv"
    # The rest of the phase-1 posture is untouched.
    assert "StrictHostKeyChecking=yes" in args
    assert "ConnectTimeout=10" in args
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["SSH_ASKPASS"].startswith(str(Path(os.environ["REEVE_STATE_DIR"])))
    assert env.get("DISPLAY"), "older builds need a display to consider askpass"


def test_askpass_files_are_private_and_removed_after_the_call():
    runner = RecordingRunner(FakeSsh())
    test_connection(PROFILE, runner=runner, password=PASSWORD)

    files = runner.last["files"]
    assert len(files) == 2
    assert runner.secret(runner.last) == PASSWORD
    for name, info in files.items():
        if not name.endswith(".secret"):
            assert PASSWORD not in str(info["content"]), (
                "the helper must point at the secret, not contain it"
            )
    if sys.platform != "win32":
        secret = next(v for k, v in files.items() if k.endswith(".secret"))
        helper = next(v for k, v in files.items() if not k.endswith(".secret"))
        assert secret["mode"] == 0o600
        assert helper["mode"] == 0o700
    assert _askpass_files() == [], "the helper and secret must not outlive the call"


def test_password_never_rides_argv_or_the_inherited_environment():
    runner = RecordingRunner(FakeSsh())
    test_connection(PROFILE, runner=runner, password=PASSWORD)

    assert PASSWORD not in " ".join(runner.last["args"])
    assert all(PASSWORD not in str(value) for value in runner.last["env"].values())


def test_askpass_files_are_removed_when_ssh_is_missing():
    result = test_connection(
        PROFILE, runner=RecordingRunner(FakeSsh(error=FileNotFoundError("ssh"))), password=PASSWORD
    )
    assert result["ok"] is False
    assert "was not found" in result["error"]
    assert _askpass_files() == []


def test_failed_password_auth_says_so_and_still_cleans_up():
    runner = RecordingRunner(FakeSsh.auth_failure())
    result = test_connection(PROFILE, runner=runner, password=PASSWORD)
    assert result["ok"] is False
    assert "Authentication failed (password)" in result["error"]
    assert len(runner.last["files"]) == 2
    assert _askpass_files() == []


def test_ssh_argv_keeps_batch_mode_only_without_a_password():
    assert "BatchMode=yes" in ssh_argv({"host": "box", "port": 22})
    assert "BatchMode=yes" not in ssh_argv({"host": "box", "port": 22}, password=True)
    assert "StrictHostKeyChecking=yes" in ssh_argv(
        {"host": "box", "port": 22}, password=True
    )


def test_remote_world_with_a_password_askpasses_every_call():
    host = FakeRemoteHost(directories={REMOTE})
    runner = RecordingRunner(FakeSsh(host=host))
    world = remote_world(PROFILE, REMOTE, runner=runner, password=PASSWORD)

    assert world.executor.run("pwd")["exit_code"] == 0
    assert len(runner.seen) >= 2, "the probe and the command both went through askpass"
    for event in runner.seen:
        assert "BatchMode=yes" not in event["args"]
        assert event["env"]["SSH_ASKPASS_REQUIRE"] == "force"
        assert runner.secret(event) == PASSWORD
    assert _askpass_files() == []


# -- the credential contract ------------------------------------------------------------


def test_remember_persists_in_the_credential_layer_never_the_file(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})

    target = credential_target(created["id"])
    assert credentials.values[target] == PASSWORD
    assert created["remembered"] is True and created["has_password"] is True
    assert "password" not in created

    raw = (tmp_path / "remote-environments.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw
    assert json.loads(raw)["profiles"][0]["remembered"] is True


def test_transient_password_is_not_persisted(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": False})

    assert credentials.values == {}
    assert created["remembered"] is False and created["has_password"] is False
    raw = (tmp_path / "remote-environments.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw


def test_remember_without_a_password_is_refused(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        store.create({**PROFILE, "remember": True})
    assert "Enter a password to remember it" in str(excinfo.value)
    assert store.list() == []

    created = store.create(PROFILE)
    with pytest.raises(ValueError):
        store.update(created["id"], {"remember": True})


def test_editing_without_retyping_keeps_the_remembered_password(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})

    updated = store.update(
        created["id"], {"name": "Renamed", "password": "", "remember": True}
    )
    assert updated["name"] == "Renamed"
    assert updated["remembered"] is True and updated["has_password"] is True
    assert credentials.values[credential_target(created["id"])] == PASSWORD


def test_unchecking_remember_deletes_the_credential(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})

    updated = store.update(created["id"], {"remember": False})
    assert updated["remembered"] is False and updated["has_password"] is False
    assert credential_target(created["id"]) not in credentials.values
    assert credentials.deleted[-1] == credential_target(created["id"])


def test_delete_cleans_the_credential(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})

    assert store.delete(created["id"]) is True
    assert credential_target(created["id"]) not in credentials.values
    assert credentials.deleted[-1] == credential_target(created["id"])


def test_remembered_password_drives_the_probe(tmp_path):
    runner = RecordingRunner(FakeSsh())
    credentials = MemoryCredentials()
    store = _store(tmp_path, runner=runner, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})

    result = store.test_connection(created["id"])
    assert result["ok"] is True
    assert "BatchMode=yes" not in runner.last["args"]
    assert runner.secret(runner.last) == PASSWORD


def test_transient_password_overrides_the_remembered_one(tmp_path):
    runner = RecordingRunner(FakeSsh())
    credentials = MemoryCredentials()
    store = _store(tmp_path, runner=runner, credentials=credentials)
    created = store.create({**PROFILE, "password": "remembered-pw", "remember": True})

    result = store.test_connection(created["id"], password="transient-pw")
    assert result["ok"] is True
    assert runner.secret(runner.last) == "transient-pw"
    # Testing with a typed password does not rewrite what is remembered.
    assert credentials.values[credential_target(created["id"])] == "remembered-pw"


def test_missing_remembered_password_reports_authentication_failed_password(tmp_path):
    runner = FakeSsh()
    credentials = MemoryCredentials()
    store = _store(tmp_path, runner=runner, credentials=credentials)
    created = store.create({**PROFILE, "password": PASSWORD, "remember": True})
    credentials.values.clear()

    result = store.test_connection(created["id"])
    assert result["ok"] is False
    assert "Authentication failed (password)" in result["error"]
    assert "could not be found" in result["error"]
    assert runner.calls == [], "no ssh call should run without a usable password"


def test_load_password_is_none_unless_remembered(tmp_path):
    credentials = MemoryCredentials()
    store = _store(tmp_path, credentials=credentials)
    transient = store.create({**PROFILE, "password": PASSWORD, "remember": False})
    assert store.load_password(transient["id"]) is None

    remembered = store.create({**PROFILE, "password": "kept-pw", "remember": True})
    assert store.load_password(remembered["id"]) == "kept-pw"

    credentials.values.clear()
    assert store.load_password(remembered["id"]) is None


# -- the HTTP surface -------------------------------------------------------------------


def test_routes_never_echo_the_password(tmp_path):
    credentials = MemoryCredentials()
    client = _client(tmp_path, credentials=credentials)

    created = client.post(
        "/v1/remote-environments",
        json={**PROFILE, "password": PASSWORD, "remember": True},
    )
    assert created.status_code == 200
    environment = created.json()["environment"]
    assert environment["remembered"] is True
    assert environment["has_password"] is True
    assert PASSWORD not in created.text
    assert "password" not in environment
    assert credentials.values[credential_target(environment["id"])] == PASSWORD

    listed = client.get("/v1/remote-environments")
    assert PASSWORD not in listed.text
    row = listed.json()["environments"][0]
    assert row["remembered"] is True and row["has_password"] is True
    assert "password" not in row

    detail = client.patch(
        f"/v1/remote-environments/{environment['id']}", json={"name": "Renamed"}
    )
    assert PASSWORD not in detail.text


def test_route_test_uses_the_transient_password(tmp_path):
    runner = RecordingRunner(FakeSsh())
    client = _client(tmp_path, runner=runner)

    profile = client.post("/v1/remote-environments", json=PROFILE).json()["environment"]
    result = client.post(
        f"/v1/remote-environments/{profile['id']}/test",
        json={"password": PASSWORD},
    ).json()

    assert result["ok"] is True
    assert "BatchMode=yes" not in runner.last["args"]
    assert runner.secret(runner.last) == PASSWORD


def test_route_remember_without_a_password_is_a_400(tmp_path):
    client = _client(tmp_path)
    refused = client.post("/v1/remote-environments", json={**PROFILE, "remember": True})
    assert refused.status_code == 400
    assert "Enter a password to remember it" in refused.json()["error"]


# -- the credential stores --------------------------------------------------------------


def test_unavailable_credential_store_degrades_with_a_clear_error():
    store = UnavailableCredentialStore()
    assert store.available is False
    assert store.get("anything") is None
    assert store.delete("anything") is False
    with pytest.raises(CredentialStoreUnavailable) as excinfo:
        store.put("anything", "secret")
    assert "Windows Credential Manager" in str(excinfo.value)


@pytest.mark.skipif(
    sys.platform == "win32", reason="non-Windows has no credential store"
)
def test_non_windows_default_store_is_unavailable():
    assert default_credential_store().available is False


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows Credential Manager round trip"
)
def test_windows_credential_manager_round_trip():
    store = WindowsCredentialStore()
    target = credential_target(f"pytest-{uuid.uuid4().hex}")
    try:
        assert store.get(target) is None
        store.put(target, PASSWORD)
        assert store.get(target) == PASSWORD
        store.put(target, "updated-pw")
        assert store.get(target) == "updated-pw"
        assert store.delete(target) is True
        assert store.get(target) is None
        assert store.delete(target) is False
    finally:
        store.delete(target)
