"""SSH remote environments, phase 1: profiles are local config and Test Connection is real.

The store's runner seam is the only way these tests touch "ssh" — `FakeSsh` stands in for
the system binary, so nothing here spawns a process, reads ~/.ssh, or touches a network.
The tests pin four things: CRUD + validation over the private profiles file, the exact ssh
argv (BatchMode/StrictHostKeyChecking/ConnectTimeout, optional -p/-i/known_hosts), the
probe parsing + actionable failure messages, and the HTTP surface (CRUD, 400s, 404s, the
test route, OpenAPI).
"""

from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from harness.orchestration.remote_environments import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_SSH_PORT, INSPECT_SENTINEL, RemoteEnvironmentStore, ssh_argv, test_connection
from harness.interface.server import SessionManager, create_app
from harness.testing.fake_ssh import FakeSsh

PROFILE = {
    "name": "Build server",
    "host": "dev.example.com",
    "user": "ubuntu",
    "port": 2222,
    "identity_file": "~/.ssh/id_ed25519",
}


@pytest.fixture(autouse=True)
def _clean_ssh_env(monkeypatch):
    monkeypatch.delenv("REEVE_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.delenv("REEVE_SSH_PATH", raising=False)


def _store(tmp_path, runner=None, **kwargs) -> RemoteEnvironmentStore:
    return RemoteEnvironmentStore(tmp_path / "remote-environments.json", runner=runner, **kwargs)


def _client(tmp_path, runner=None) -> TestClient:
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    if runner is not None:
        app.state.remote_environments._runner = runner
    return TestClient(app)


# -- profiles: local config, nothing secret -------------------------------------------


def test_create_list_round_trip_on_a_private_file(tmp_path):
    store = _store(tmp_path)
    created = store.create(PROFILE)

    assert created["id"]
    assert created["port"] == 2222
    assert created["created_at"].endswith("Z") and created["updated_at"].endswith("Z")
    # Exactly the profile fields: no password, no key material, identity_file is a PATH.
    # `remembered` is the JSON flag and `has_password` is the wire probe (password
    # auth); neither is ever the secret itself.
    assert set(created) == {
        "id",
        "name",
        "host",
        "user",
        "port",
        "identity_file",
        "remembered",
        "has_password",
        "created_at",
        "updated_at",
    }
    assert created["remembered"] is False and created["has_password"] is False
    assert "password" not in created
    assert created["identity_file"] == "~/.ssh/id_ed25519"
    assert [p["id"] for p in store.list()] == [created["id"]]

    payload = json.loads((tmp_path / "remote-environments.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["profiles"][0]["host"] == "dev.example.com"
    assert "password" not in payload["profiles"][0]


def test_defaults_and_optional_fields(tmp_path):
    created = _store(tmp_path).create({"name": "Box", "host": "box"})
    assert created["port"] == DEFAULT_SSH_PORT
    assert created["user"] is None
    assert created["identity_file"] is None


def test_profiles_sort_by_name(tmp_path):
    store = _store(tmp_path)
    store.create({"name": "zeta", "host": "z"})
    store.create({"name": "Alpha", "host": "a"})
    assert [p["name"] for p in store.list()] == ["Alpha", "zeta"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"name": "  ", "host": "h"}, "Name is required."),
        ({"name": "n", "host": " "}, "SSH host is required."),
        ({"name": "n", "host": "-oProxyCommand=evil"}, "SSH host must be"),
        ({"name": "n", "host": "bad host"}, "SSH host must be"),
        ({"name": "n", "host": "h", "port": 0}, "SSH port must be"),
        ({"name": "n", "host": "h", "port": 70000}, "SSH port must be"),
        ({"name": "n", "host": "h", "port": "abc"}, "SSH port must be"),
        ({"name": "n", "host": "h", "user": "bad user"}, "SSH user contains"),
        ({"name": "n", "host": "h", "identity_file": "/x\n/y"}, "identity file"),
    ],
)
def test_validation_refuses_bad_input(tmp_path, payload, message):
    store = _store(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        store.create(payload)
    assert message in str(excinfo.value)
    assert store.list() == []


def test_update_keeps_identity_and_bumps_updated_at(tmp_path):
    store = _store(tmp_path)
    created = store.create(PROFILE)
    updated = store.update(created["id"], {"name": "Renamed", "port": 22})
    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["name"] == "Renamed" and updated["port"] == 22
    with pytest.raises(ValueError):
        store.update(created["id"], {"host": ""})
    with pytest.raises(KeyError):
        store.update("missing", {"name": "x"})


def test_delete_round_trip(tmp_path):
    store = _store(tmp_path)
    created = store.create(PROFILE)
    assert store.delete(created["id"]) is True
    assert store.delete(created["id"]) is False
    with pytest.raises(KeyError):
        store.get(created["id"])
    assert store.list() == []


def test_torn_file_degrades_to_no_profiles(tmp_path):
    path = tmp_path / "remote-environments.json"
    path.write_text("{ not json", encoding="utf-8")
    assert _store(tmp_path).list() == []


# -- the probe: real ssh argv, faked process ------------------------------------------


def test_probe_success_normalizes_and_carries_the_posture(tmp_path):
    fake = FakeSsh(platform="Darwin", arch="aarch64", home="/Users/dev")
    store = _store(tmp_path, runner=fake)
    created = store.create(PROFILE)

    result = store.test_connection(created["id"])
    assert result == {
        "ok": True,
        "platform": "darwin",
        "arch": "arm64",
        "home": "/Users/dev",
        "error": None,
    }

    args = fake.last_args
    assert args[0] == "ssh"  # the SYSTEM binary; options are argv entries, never a shell string
    for flag in (
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        f"ConnectTimeout={int(DEFAULT_CONNECT_TIMEOUT_SECONDS)}",
    ):
        assert flag in args
    assert fake.last_destination == "ubuntu@dev.example.com"
    assert args[args.index("-p") + 1] == "2222"
    identity = args[args.index("-i") + 1]
    assert not identity.startswith("~") and identity.endswith("id_ed25519")
    assert "uname -s" in fake.last_remote_command
    assert INSPECT_SENTINEL in fake.last_remote_command


def test_default_port_is_not_forced_so_ssh_config_aliases_survive(tmp_path):
    fake = FakeSsh()
    store = _store(tmp_path, runner=fake)
    store.create({"name": "Alias", "host": "my-alias"})
    profile = store.list()[0]
    store.test_connection(profile["id"])
    assert "-p" not in fake.last_args


def test_known_hosts_passes_through_only_when_configured(tmp_path, monkeypatch):
    forced = _store(tmp_path, runner=FakeSsh(), known_hosts_file="/etc/ssh/known_hosts")
    forced.create({"name": "n", "host": "h"})
    forced.test_connection(forced.list()[0]["id"])
    assert "UserKnownHostsFile=/etc/ssh/known_hosts" in forced._runner.last_args

    monkeypatch.setenv("REEVE_SSH_KNOWN_HOSTS_FILE", "/custom/known_hosts")
    env_store = _store(tmp_path / "env", runner=FakeSsh())
    env_store.create({"name": "n", "host": "h"})
    env_store.test_connection(env_store.list()[0]["id"])
    assert "UserKnownHostsFile=/custom/known_hosts" in env_store._runner.last_args

    monkeypatch.delenv("REEVE_SSH_KNOWN_HOSTS_FILE")
    bare = _store(tmp_path / "bare", runner=FakeSsh())
    bare.create({"name": "n", "host": "h"})
    bare.test_connection(bare.list()[0]["id"])
    assert not any("UserKnownHostsFile" in arg for arg in bare._runner.last_args)


def test_ssh_path_override_is_the_configured_binary(monkeypatch):
    monkeypatch.setenv("REEVE_SSH_PATH", "/opt/openssh/bin/ssh")
    argv = ssh_argv({"host": "h", "port": 22})
    assert argv[0] == "/opt/openssh/bin/ssh"


def test_auth_failure_is_actionable():
    result = test_connection(PROFILE, runner=FakeSsh.auth_failure())
    assert result["ok"] is False
    assert "Authentication failed" in result["error"]
    assert "public key" in result["error"]
    # No password was supplied, so the message is not the password variant.
    assert "(password)" not in result["error"]


def test_host_key_failure_names_known_hosts():
    unknown = test_connection(PROFILE, runner=FakeSsh.host_key_failure())
    assert unknown["ok"] is False
    assert "known_hosts" in unknown["error"]
    changed = test_connection(
        PROFILE,
        runner=FakeSsh(
            returncode=255,
            stderr="@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@",
        ),
    )
    assert "changed" in changed["error"]


def test_timeout_and_missing_ssh_are_distinct():
    timed_out = test_connection(
        PROFILE, runner=FakeSsh(error=subprocess.TimeoutExpired("ssh", 30))
    )
    assert "Timed out" in timed_out["error"]
    missing = test_connection(PROFILE, runner=FakeSsh(error=FileNotFoundError("ssh")))
    assert "was not found" in missing["error"]


def test_unsupported_platform_is_named():
    result = test_connection(PROFILE, runner=FakeSsh.unsupported())
    assert result["ok"] is False
    assert "FreeBSD" in result["error"]
    assert "x64 or arm64" in result["error"]


def test_foreign_or_invalid_probe_output_is_refused():
    foreign = test_connection(PROFILE, runner=FakeSsh(stdout="hello from the motd\n"))
    assert foreign["ok"] is False
    assert "invalid probe payload" in foreign["error"]
    bad_home = test_connection(PROFILE, runner=FakeSsh(home="relative/home"))
    assert bad_home["ok"] is False
    assert "HOME" in bad_home["error"]


def test_stderr_output_is_bounded_and_sentinel_noise_stripped():
    noisy = FakeSsh(returncode=255, stderr="x" * 50_000 + INSPECT_SENTINEL)
    result = test_connection(PROFILE, runner=noisy)
    assert len(result["error"]) < 4_000
    assert INSPECT_SENTINEL not in result["error"]


# -- HTTP surface ----------------------------------------------------------------------


def test_routes_crud_validation_and_404s(tmp_path):
    client = _client(tmp_path)
    assert client.get("/v1/remote-environments").json() == {"environments": []}

    created = client.post("/v1/remote-environments", json=PROFILE)
    assert created.status_code == 200
    profile = created.json()["environment"]
    assert [p["id"] for p in client.get("/v1/remote-environments").json()["environments"]] == [
        profile["id"]
    ]

    patched = client.patch(
        f"/v1/remote-environments/{profile['id']}", json={"name": "Renamed"}
    )
    assert patched.status_code == 200
    assert patched.json()["environment"]["name"] == "Renamed"

    invalid = client.post("/v1/remote-environments", json={"name": "", "host": ""})
    assert invalid.status_code == 400 and invalid.json()["ok"] is False
    invalid_patch = client.patch(
        f"/v1/remote-environments/{profile['id']}", json={"port": -1}
    )
    assert invalid_patch.status_code == 400

    assert client.patch("/v1/remote-environments/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/v1/remote-environments/nope").status_code == 404
    assert client.post("/v1/remote-environments/nope/test").status_code == 404

    deleted = client.delete(f"/v1/remote-environments/{profile['id']}")
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    assert client.get("/v1/remote-environments").json() == {"environments": []}


def test_route_test_success_runs_through_the_runner_seam(tmp_path):
    fake = FakeSsh(platform="Linux", arch="aarch64", home="/home/ci")
    client = _client(tmp_path, runner=fake)
    profile = client.post("/v1/remote-environments", json=PROFILE).json()["environment"]

    result = client.post(f"/v1/remote-environments/{profile['id']}/test").json()
    assert result == {
        "ok": True,
        "platform": "linux",
        "arch": "arm64",
        "home": "/home/ci",
        "error": None,
    }
    assert fake.calls and fake.last_destination == "ubuntu@dev.example.com"


def test_route_test_reports_failures_inline(tmp_path):
    client = _client(tmp_path, runner=FakeSsh.host_key_failure())
    profile = client.post("/v1/remote-environments", json=PROFILE).json()["environment"]
    failed = client.post(f"/v1/remote-environments/{profile['id']}/test").json()
    assert failed["ok"] is False and "known_hosts" in failed["error"]

    unsupported_client = _client(tmp_path / "unsupported", runner=FakeSsh.unsupported())
    unsupported = unsupported_client.post(
        "/v1/remote-environments", json=PROFILE
    ).json()["environment"]
    refused = unsupported_client.post(
        f"/v1/remote-environments/{unsupported['id']}/test"
    ).json()
    assert refused["ok"] is False and "FreeBSD" in refused["error"]


def test_openapi_advertises_the_remote_surface(tmp_path):
    client = _client(tmp_path)
    schema = client.get("/openapi.json").json()
    collection = schema["paths"]["/v1/remote-environments"]
    assert {"get", "post"} <= set(collection)
    item = schema["paths"]["/v1/remote-environments/{environment_id}"]
    assert {"patch", "delete"} <= set(item)
    assert "post" in schema["paths"]["/v1/remote-environments/{environment_id}/test"]
