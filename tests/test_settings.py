"""The model key and the other settings the app keeps for itself. The desktop app's server does
not inherit the shell's environment, so the key may be only in the SecretStore. The key is read from
the environment first, the settings never give it back, and what is set survives a restart. No
network, no model calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import resolve_api_key
from harness.safety.secrets import SecretStore


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "data"


def _client(data_dir):
    return TestClient(create_app(SessionManager(data_dir=data_dir)))


def test_the_key_in_the_environment_wins_and_the_stored_one_is_next(monkeypatch, tmp_path):
    secrets = SecretStore(path=tmp_path / "secrets.json")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_api_key(secrets) is None
    secrets.put("provider:openai", {"type": "api_key", "api_key": "sk-store-999"})
    assert resolve_api_key(secrets) == "sk-store-999"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123")
    assert resolve_api_key(secrets) == "sk-env-123"


def test_a_key_set_in_the_app_is_kept_and_never_given_back(data_dir):
    client = _client(data_dir)
    fresh = client.get("/v1/settings").json()
    assert (fresh["has_key"], fresh["source"], fresh["provider"], fresh["onboarded"]) == (False, None, "openai", False)
    assert fresh["model"] in fresh["models"]
    kept = client.post("/v1/settings/model-key", json={"api_key": "sk-secret-xyz"}).json()
    assert (kept["ok"], kept["has_key"], kept["source"]) == (True, True, "store")
    after = client.get("/v1/settings").json()
    assert after["has_key"] is True
    assert "sk-secret-xyz" not in str(kept) and "api_key" not in after
    assert client.post("/v1/settings/model-key", json={"api_key": "  "}).json()["ok"] is False


def test_the_default_model_and_onboarding_survive_a_restart(data_dir):
    client = _client(data_dir)
    assert client.post("/v1/settings/default-model", json={"model": "gpt-4o"}).json()["model"] == "gpt-4o"
    assert client.post("/v1/settings/onboarded", json={"value": True}).json()["onboarded"] is True
    assert client.post("/v1/settings/default-model", json={"model": " "}).json()["ok"] is False
    restarted = SessionManager(data_dir=data_dir)  # read back from prefs.json
    assert restarted.model == "gpt-4o"
    settings = restarted.get_settings()
    assert settings["onboarded"] is True and settings["model"] == "gpt-4o"


def test_the_scratch_folder_is_made_when_set_and_is_where_scratch_space_goes(data_dir, tmp_path):
    client = _client(data_dir)
    assert client.get("/v1/settings").json()["scratch_base"] == "~/Reeve"
    base = tmp_path / "my agent files"
    chosen = client.post("/v1/settings/scratch-base", json={"path": str(base)}).json()
    assert chosen["ok"] is True and chosen["scratch_base"] == str(base) and base.is_dir()
    assert client.post("/v1/settings/scratch-base", json={"path": " "}).json()["ok"] is False
    restarted = SessionManager(data_dir=data_dir)
    assert restarted.get_settings()["scratch_base"] == str(base)
    scratch = Path(restarted._provision_scratch("sess-xyz"))
    assert scratch == (base / "sess-xyz").resolve() and scratch.is_dir()


def test_an_ollama_model_is_listed_only_while_ollama_answers(data_dir, monkeypatch):
    """Needing no key must not mean always there: a stray ollama:<junk> preference would be listed
    for ever."""
    manager = SessionManager(data_dir=data_dir)
    manager.add_model("ollama:llama3.3")
    for alive in (False, True):
        monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self, alive=alive: alive)
        assert ("ollama:llama3.3" in manager.get_settings()["models"]) is alive
