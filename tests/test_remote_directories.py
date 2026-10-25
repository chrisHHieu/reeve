"""Remote directory browsing: the environment picker's listing.

`list_remote_directories` runs ONE framed command over the shared SSH transport — the
requested path is resolved on the host (`cd -P` + `pwd -P`) before any entry is built.
Every test goes through `FakeSsh`/`FakeRemoteHost`: no ssh binary, no network, no
`~/.ssh`. The route tests inject the same runner through `app.state.remote_environments`,
so the HTTP surface and the transport test the same seam.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness.orchestration.remote_env import RemoteSession, canonical_remote_directory, list_remote_directories, parse_directory_listing
from harness.orchestration.remote_env.transport import RemoteProtocolError
from harness.interface.server import SessionManager, create_app
from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
HOME = "/home/dev"
PROJECT = "/home/dev/project"


def _fake() -> FakeSsh:
    host = FakeRemoteHost(
        directories={
            PROJECT,
            f"{PROJECT}/src",
            f"{PROJECT}/docs",
            f"{HOME}/shared",
        }
    )
    host.write(f"{PROJECT}/readme.md", "hello\n")  # a file is not a folder
    return FakeSsh(host=host)


def _session(fake: FakeSsh) -> RemoteSession:
    return RemoteSession(PROFILE, runner=fake)


def _client(tmp_path, runner) -> TestClient:
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    app.state.remote_environments._runner = runner
    return TestClient(app)


def _profile_id(client: TestClient) -> str:
    return client.post("/v1/remote-environments", json=PROFILE).json()["environment"]["id"]


# -- the listing primitive ---------------------------------------------------------------


def test_lists_immediate_subdirectories_sorted():
    listing = list_remote_directories(_session(_fake()), PROJECT)
    assert listing["path"] == PROJECT
    assert [entry["name"] for entry in listing["entries"]] == ["docs", "src"]
    assert listing["entries"][0]["path"] == f"{PROJECT}/docs"
    # The file planted in the directory is not a folder entry.
    assert all(entry["name"] != "readme.md" for entry in listing["entries"])


def test_omitted_or_empty_path_defaults_to_home():
    fake = _fake()
    for path in (None, "", "   "):
        listing = list_remote_directories(_session(fake), path)
        assert listing["path"] == HOME
        assert [entry["name"] for entry in listing["entries"]] == ["project", "shared"]


def test_missing_directory_is_a_value_error():
    with pytest.raises(ValueError, match="does not exist"):
        list_remote_directories(_session(_fake()), f"{HOME}/nope")


def test_permission_denied_is_a_permission_error():
    host = FakeRemoteHost(
        directories={PROJECT, f"{PROJECT}/secret"},
        denied={f"{PROJECT}/secret"},
    )
    with pytest.raises(PermissionError, match="Permission denied"):
        list_remote_directories(_session(FakeSsh(host=host)), f"{PROJECT}/secret")


def test_traversal_is_normalized_on_the_host():
    fake = _fake()
    # `..` is resolved by the host's own `pwd -P`, and the resolved base is what comes
    # back: no spelling of the request can make the listing name a directory outside it.
    listing = list_remote_directories(_session(fake), f"{PROJECT}/src/..")
    assert listing["path"] == PROJECT
    assert [entry["path"] for entry in listing["entries"]] == [
        f"{PROJECT}/docs",
        f"{PROJECT}/src",
    ]

    up = list_remote_directories(_session(fake), f"{PROJECT}/../shared")
    assert up["path"] == f"{HOME}/shared"


def test_relative_paths_are_refused_before_any_round_trip():
    fake = _fake()
    with pytest.raises(ValueError, match="absolute"):
        list_remote_directories(_session(fake), "home/dev/project")
    assert fake.calls == [], "a relative path is refused before ssh is asked"


def test_listing_is_bounded():
    host = FakeRemoteHost(directories={PROJECT})
    for index in range(10):
        host._ensure_dir(f"{PROJECT}/dir-{index:02d}")
    listing = list_remote_directories(_session(FakeSsh(host=host)), PROJECT, limit=3)
    assert [entry["name"] for entry in listing["entries"]] == [
        "dir-00",
        "dir-01",
        "dir-02",
    ]


# -- the parser's hostile-input rules ----------------------------------------------------


def test_parser_requires_an_absolute_host_resolved_base():
    with pytest.raises(RemoteProtocolError, match="absolute"):
        parse_directory_listing("relative/base\n")
    with pytest.raises(RemoteProtocolError, match="no directory listing"):
        parse_directory_listing("")


def test_parser_skips_everything_not_directly_under_the_base():
    parsed = parse_directory_listing(
        "/srv\n/srv/one\n/other/two\n/srv/nested/three\n/srv/one\n..\n"
    )
    assert [entry["name"] for entry in parsed["entries"]] == ["one"]


def test_canonical_remote_directory_refuses_newlines_and_nul():
    with pytest.raises(ValueError, match="newlines"):
        canonical_remote_directory("/srv/a\nb")
    with pytest.raises(ValueError, match="newlines"):
        canonical_remote_directory("/srv/a\0b")


# -- the HTTP surface --------------------------------------------------------------------


def test_route_lists_directories_through_the_runner_seam(tmp_path):
    fake = _fake()
    client = _client(tmp_path, fake)
    environment_id = _profile_id(client)

    listed = client.get(
        f"/v1/remote-environments/{environment_id}/directories",
        params={"path": PROJECT},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True and body["path"] == PROJECT
    assert [entry["name"] for entry in body["entries"]] == ["docs", "src"]
    assert fake.calls, "the listing ran through the store's injected runner"


def test_route_defaults_to_home_and_reports_refusals_inline(tmp_path):
    host = FakeRemoteHost(
        directories={PROJECT, f"{PROJECT}/secret"},
        denied={f"{PROJECT}/secret"},
    )
    client = _client(tmp_path, FakeSsh(host=host))
    environment_id = _profile_id(client)
    base = f"/v1/remote-environments/{environment_id}/directories"

    defaulted = client.get(base).json()
    assert defaulted["ok"] is True and defaulted["path"] == HOME

    missing = client.get(base, params={"path": f"{HOME}/nope"})
    assert missing.status_code == 400
    assert "does not exist" in missing.json()["error"]

    relative = client.get(base, params={"path": "home/dev/project"})
    assert relative.status_code == 400
    assert "absolute" in relative.json()["error"]

    denied = client.get(base, params={"path": f"{PROJECT}/secret"})
    assert denied.status_code == 400
    assert "Permission denied" in denied.json()["error"]

    assert client.get("/v1/remote-environments/nope/directories").status_code == 404


def test_route_reports_typed_connection_failures(tmp_path):
    client = _client(tmp_path, FakeSsh.auth_failure())
    environment_id = _profile_id(client)
    refused = client.get(
        f"/v1/remote-environments/{environment_id}/directories",
        params={"path": PROJECT},
    )
    assert refused.status_code == 400
    assert "Authentication failed" in refused.json()["error"]


class _MemoryCredentials:
    """The credential seam as a dict — never the real Credential Manager."""

    available = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, target: str):
        return self.values.get(target)

    def put(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


def test_remembered_password_rides_the_askpass_seam(tmp_path, monkeypatch):
    """A remembered secret reaches the listing call exactly as Test Connection resolves it."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    fake = _fake()
    store = RemoteEnvironmentStore(
        tmp_path / "remote-environments.json",
        runner=fake,
        credentials=_MemoryCredentials(),
    )
    profile = store.create({**PROFILE, "password": "hunter2", "remember": True})
    listing = store.list_directories(profile["id"], PROJECT)
    assert listing["path"] == PROJECT
    assert fake.last_env, "the listing ran under the askpass environment"


def test_openapi_advertises_directory_browsing(tmp_path):
    client = _client(tmp_path, _fake())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/remote-environments/{environment_id}/directories"][
        "get"
    ]
    parameters = {parameter["name"] for parameter in operation.get("parameters", [])}
    assert "path" in parameters
