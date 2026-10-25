"""Where a one-click sign-in's client id comes from: the environment, then `.env`, then the ids
the private repository carries."""

import json

from harness.safety.secrets import client_env, state_dir
from harness.safety.secrets import store


def _builtin(monkeypatch, tmp_path, values):
    path = tmp_path / "client_ids.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setattr(store, "BUILTIN_CLIENTS", path)


def _dotenv(text):
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".env").write_text(text, encoding="utf-8")


def test_with_nothing_set_there_is_no_id(monkeypatch):
    monkeypatch.delenv("REEVE_TEST_CLIENT_ID", raising=False)
    assert client_env("REEVE_TEST_CLIENT_ID") == ""


def test_the_carried_id_is_the_last_resort(monkeypatch, tmp_path):
    monkeypatch.delenv("REEVE_TEST_CLIENT_ID", raising=False)
    _builtin(monkeypatch, tmp_path, {"REEVE_TEST_CLIENT_ID": " carried-id "})
    assert client_env("REEVE_TEST_CLIENT_ID") == "carried-id"

    _dotenv("REEVE_TEST_CLIENT_ID=dotenv-id\n")
    assert client_env("REEVE_TEST_CLIENT_ID") == "dotenv-id"

    monkeypatch.setenv("REEVE_TEST_CLIENT_ID", "env-id")
    assert client_env("REEVE_TEST_CLIENT_ID") == "env-id"


def test_a_broken_carried_file_is_no_id(monkeypatch, tmp_path):
    monkeypatch.delenv("REEVE_TEST_CLIENT_ID", raising=False)
    path = tmp_path / "client_ids.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(store, "BUILTIN_CLIENTS", path)
    assert client_env("REEVE_TEST_CLIENT_ID") == ""
    _builtin(monkeypatch, tmp_path, ["REEVE_TEST_CLIENT_ID"])
    assert client_env("REEVE_TEST_CLIENT_ID") == ""
